"""Tests for the audience-reducer streaming consumer."""

from __future__ import annotations

import base64
import json

import boto3
import pytest
from moto import mock_aws

from audience_ingress.handlers import audience_reducer
from audience_ingress.model.types import (
    STATE_ELIGIBLE,
    STATE_INELIGIBLE,
    build_partition_key,
)

TABLE_NAME = "dev-audience-membership"


@pytest.fixture
def membership_table():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-2")
        ddb.create_table(
            TableName=TABLE_NAME,
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
        yield ddb.Table(TABLE_NAME)


def _kinesis_event(records: list[dict]) -> dict:
    return {
        "Records": [
            {"kinesis": {"data": base64.b64encode(json.dumps(r).encode()).decode()}}
            for r in records
        ]
    }


def _reset_store(monkeypatch, table) -> None:
    # Point the handler at the moto table and stub MCI to echo internal ids.
    monkeypatch.setattr(audience_reducer, "MEMBERSHIP_TABLE_NAME", TABLE_NAME)
    monkeypatch.setattr(audience_reducer, "_store", None)
    monkeypatch.setattr(
        audience_reducer,
        "_get_store",
        lambda: audience_reducer.MembershipStore(table),
    )

    def fake_resolve(function_name, customer_keys, *, qualifier, read_only=False):
        out: dict[str, dict[str, str]] = {}
        for k in customer_keys:
            out.setdefault(k.tenant_id, {})[k.customer_id] = f"int-{k.customer_id}"
        return out

    monkeypatch.setattr(audience_reducer, "resolve_internal_ids", fake_resolve)


def test_reducer_add_action_writes_eligible(monkeypatch, membership_table) -> None:
    _reset_store(monkeypatch, membership_table)
    event = _kinesis_event(
        [
            {
                "tenant_id": "tenant-abc",
                "external_customer_id": "cust-1",
                "audience_changes": [{"audience_id": "aud-a", "action": "add"}],
                "timestamp": "2026-07-05T12:00:00+00:00",
            }
        ]
    )
    audience_reducer.handler(event)

    item = membership_table.get_item(
        Key={"partition_key": build_partition_key("tenant-abc", "aud-a"), "sort_key": "int-cust-1"}
    )["Item"]
    assert item["state"] == STATE_ELIGIBLE
    assert item["internal_customer_id"] == "int-cust-1"


def test_reducer_delete_action_soft_deletes(monkeypatch, membership_table) -> None:
    _reset_store(monkeypatch, membership_table)
    event = _kinesis_event(
        [
            {
                "tenant_id": "tenant-abc",
                "external_customer_id": "cust-1",
                "audience_changes": [{"audience_id": "aud-a", "action": "delete"}],
                "timestamp": "2026-07-05T12:00:00+00:00",
            }
        ]
    )
    audience_reducer.handler(event)

    item = membership_table.get_item(
        Key={"partition_key": build_partition_key("tenant-abc", "aud-a"), "sort_key": "int-cust-1"}
    )["Item"]
    # Soft delete: row present, state INELIGIBLE, never removed.
    assert item["state"] == STATE_INELIGIBLE


def test_reducer_parallel_no_lost_writes(monkeypatch, membership_table) -> None:
    _reset_store(monkeypatch, membership_table)
    # 1,000 distinct members added in one batch, written with 50x parallelism.
    # Scaled down from the spec's 50k for a fast unit test; the property under
    # test is the same: concurrent writes lose nothing.
    records = [
        {
            "tenant_id": "tenant-abc",
            "external_customer_id": f"cust-{i}",
            "audience_changes": [{"audience_id": "aud-a", "action": "add"}],
            "timestamp": "2026-07-05T12:00:00+00:00",
        }
        for i in range(1_000)
    ]
    audience_reducer.handler(_kinesis_event(records))

    scanned = membership_table.scan()["Items"]
    assert len(scanned) == 1_000
    assert all(it["state"] == STATE_ELIGIBLE for it in scanned)
