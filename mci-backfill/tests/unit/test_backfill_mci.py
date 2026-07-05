"""Unit tests for the backfill-mci Lambda.

Two layers:
  1. Isolated: a capturing fake for resolve_internal_ids verifies backfill builds
     the right CustomerKeys (internal == external under day0, unset otherwise).
  2. End to end through real MCI: a fake invoker routes the request into the
     actual mci-core get-internal handler backed by moto DynamoDB. This proves
     the day-0 mapping lands as internal == external and that re-running the same
     batch is idempotent, exercising the caller-supplied-internal-id path we
     added to mci-core.
"""

from __future__ import annotations

import boto3
import mci.handlers.get_internal_customer_ids as mci_h
from moto import mock_aws

import backfill.handlers.backfill_mci as bf

MCI_TABLE = "test-master-customer-index"


# ----- layer 1: key construction (isolated) ----------------------------------


def test_day0_builds_internal_equals_external(monkeypatch):
    captured: dict = {}

    def fake_resolve(function_name, customer_keys, *, qualifier="LIVE", **kwargs):
        captured["keys"] = customer_keys
        return {"t1": {k.customer_id: k.customer_id for k in customer_keys}}

    monkeypatch.setattr(bf, "resolve_internal_ids", fake_resolve)

    out = bf.handler({"tenant_id": "t1", "customer_ids": ["a", "b"], "day0": True})

    assert out == {"records_processed": 2}
    # every key was pinned internal == external
    assert all(k.internal_customer_id == k.customer_id for k in captured["keys"])


def test_non_day0_leaves_internal_unset(monkeypatch):
    captured: dict = {}

    def fake_resolve(function_name, customer_keys, *, qualifier="LIVE", **kwargs):
        captured["keys"] = customer_keys
        return {"t1": {k.customer_id: "generated-uuid" for k in customer_keys}}

    monkeypatch.setattr(bf, "resolve_internal_ids", fake_resolve)

    bf.handler({"tenant_id": "t1", "customer_ids": ["a"], "day0": False})

    assert captured["keys"][0].internal_customer_id is None  # MCI would mint one


# ----- layer 2: through real MCI + moto ---------------------------------------


def _create_mci_table(dynamodb):
    dynamodb.create_table(
        TableName=MCI_TABLE,
        KeySchema=[
            {"AttributeName": "partition_key", "KeyType": "HASH"},
            {"AttributeName": "sort_key", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "partition_key", "AttributeType": "S"},
            {"AttributeName": "sort_key", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _wire_backfill_to_real_mci(monkeypatch):
    """Point the mci get-internal handler at moto, and replace backfill's invoker
    with one that calls that real handler in-process."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(mci_h, "TABLE_NAME", MCI_TABLE)
    monkeypatch.setattr(mci_h, "LOG_BUCKET", "")
    monkeypatch.setattr(mci_h, "_store", None)
    monkeypatch.setattr(mci_h, "_s3_client", None)

    def fake_resolve(function_name, customer_keys, *, qualifier="LIVE", **kwargs):
        event = {
            "customer_keys": [
                {
                    "tenant_id": k.tenant_id,
                    "customer_id": k.customer_id,
                    **(
                        {"internal_customer_id": k.internal_customer_id}
                        if k.internal_customer_id is not None
                        else {}
                    ),
                }
                for k in customer_keys
            ]
        }
        return mci_h.handler(event)

    monkeypatch.setattr(bf, "resolve_internal_ids", fake_resolve)


@mock_aws
def test_day0_mapping_lands_as_internal_equals_external(monkeypatch):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_mci_table(dynamodb)
    _wire_backfill_to_real_mci(monkeypatch)

    out = bf.handler({"tenant_id": "t1", "customer_ids": ["ext-1", "ext-2"], "day0": True})
    assert out == {"records_processed": 2}

    # verify the rows in DynamoDB actually have internal == external
    table = dynamodb.Table(MCI_TABLE)
    for ext in ("ext-1", "ext-2"):
        item = table.get_item(Key={"partition_key": f"t1#{ext}", "sort_key": "NULL"})["Item"]
        assert item["internal_customer_id"] == ext


@mock_aws
def test_running_same_batch_twice_is_idempotent(monkeypatch):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_mci_table(dynamodb)
    _wire_backfill_to_real_mci(monkeypatch)

    batch = {"tenant_id": "t1", "customer_ids": ["ext-1", "ext-2"], "day0": True}
    first = bf.handler(batch)
    second = bf.handler(batch)  # must not error on the existing mappings

    assert first == second == {"records_processed": 2}

    # still exactly the two rows, unchanged
    table = dynamodb.Table(MCI_TABLE)
    scanned = table.scan()["Items"]
    assert len(scanned) == 2
    assert {i["internal_customer_id"] for i in scanned} == {"ext-1", "ext-2"}
