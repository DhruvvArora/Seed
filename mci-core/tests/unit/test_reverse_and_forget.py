"""Unit tests for the get-external and forget-external handlers."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

import mci.handlers.forget_external_customer_ids as forget_h
import mci.handlers.get_external_customer_ids as getext_h
import mci.handlers.get_internal_customer_ids as getint_h

TABLE_NAME = "test-master-customer-index"
INTERNAL_ID_GSI = "internal-customer-id-index"


def _create_table_with_gsi(dynamodb):
    """Create the table WITH the internal-customer-id GSI, mirroring Terraform."""
    dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "partition_key", "KeyType": "HASH"},
            {"AttributeName": "sort_key", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "partition_key", "AttributeType": "S"},
            {"AttributeName": "sort_key", "AttributeType": "S"},
            {"AttributeName": "internal_customer_id", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": INTERNAL_ID_GSI,
                "KeySchema": [
                    {"AttributeName": "internal_customer_id", "KeyType": "HASH"},
                    {"AttributeName": "tenant_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for mod in (getint_h, getext_h, forget_h):
        monkeypatch.setattr(mod, "TABLE_NAME", TABLE_NAME)
        monkeypatch.setattr(mod, "_store", None)
    monkeypatch.setattr(getint_h, "LOG_BUCKET", "")
    monkeypatch.setattr(forget_h, "AUDIT_BUCKET", "")
    monkeypatch.setattr(forget_h, "_s3_client", None)


def _seed(uuid_map):
    """Create some mappings via the get-internal handler; return its output."""
    keys = [{"tenant_id": t, "customer_id": e} for (t, e) in uuid_map]
    return getint_h.handler({"customer_keys": keys})


@mock_aws
def test_reverse_lookup_returns_external(env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table_with_gsi(dynamodb)

    out = _seed([("acme", "ext1"), ("acme", "ext2")])
    uuid1 = out["acme"]["ext1"]
    uuid2 = out["acme"]["ext2"]

    result = getext_h.handler({"tenant_id": "acme", "internal_customer_ids": [uuid1, uuid2]})
    assert result == {uuid1: "ext1", uuid2: "ext2"}


@mock_aws
def test_reverse_lookup_omits_unknown_uuid(env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table_with_gsi(dynamodb)

    out = _seed([("acme", "ext1")])
    uuid1 = out["acme"]["ext1"]

    result = getext_h.handler(
        {"tenant_id": "acme", "internal_customer_ids": [uuid1, "uuid-does-not-exist"]}
    )
    assert result == {uuid1: "ext1"}  # unknown uuid omitted


@mock_aws
def test_forget_deletes_and_reports(env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table_with_gsi(dynamodb)

    _seed([("acme", "ext1"), ("acme", "ext2")])

    result = forget_h.handler(
        {"tenant_id": "acme", "external_customer_ids": ["ext1", "ext2", "ext-missing"]}
    )
    assert sorted(result["deleted"]) == ["ext1", "ext2"]
    assert result["not_found"] == ["ext-missing"]


@mock_aws
def test_forget_actually_removes_mapping(env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table_with_gsi(dynamodb)

    out = _seed([("acme", "ext1")])
    original_uuid = out["acme"]["ext1"]

    forget_h.handler({"tenant_id": "acme", "external_customer_ids": ["ext1"]})

    # After deletion, resolving ext1 again creates a NEW uuid (old mapping gone).
    out2 = getint_h.handler({"customer_keys": [{"tenant_id": "acme", "customer_id": "ext1"}]})
    assert out2["acme"]["ext1"] != original_uuid


@mock_aws
def test_forget_writes_audit_log(env, monkeypatch):
    """When an audit bucket is configured, a record is written even though the
    deletion itself succeeds. Proves the compliance path runs."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table_with_gsi(dynamodb)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="audit-bucket")

    monkeypatch.setattr(forget_h, "AUDIT_BUCKET", "audit-bucket")
    monkeypatch.setattr(forget_h, "_s3_client", None)

    _seed([("acme", "ext1")])
    forget_h.handler({"tenant_id": "acme", "external_customer_ids": ["ext1"]})

    objects = s3.list_objects_v2(Bucket="audit-bucket")
    assert objects.get("KeyCount", 0) == 1
    assert objects["Contents"][0]["Key"].startswith("forget-audit/acme/")
