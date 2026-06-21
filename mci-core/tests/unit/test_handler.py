"""Unit tests for the get-internal-customer-ids handler.

Covers the spec's named cases at the handler level:
  NewMapping, ExistingMapping, ReadOnly, Deduplication
plus the nested output-shape contract.

The handler builds its store lazily from the TABLE_NAME env var, so each test
sets that var and points boto3 at moto's in-memory DynamoDB. We reset the
handler's cached singletons between tests so they pick up the mocked resource.
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

import mci.handlers.get_internal_customer_ids as h

TABLE_NAME = "test-master-customer-index"


def _create_table(dynamodb):
    dynamodb.create_table(
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


@pytest.fixture
def mci_env(monkeypatch):
    """Set env vars and reset the handler's cached singletons for each test."""
    # The handler builds its own boto3 resource with no explicit region, so we
    # provide one here the way the Lambda runtime would in production.
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(h, "TABLE_NAME", TABLE_NAME)
    monkeypatch.setattr(h, "LOG_BUCKET", "")  # skip S3 logging in these tests
    monkeypatch.setattr(h, "_store", None)
    monkeypatch.setattr(h, "_s3_client", None)


@mock_aws
def test_new_mapping_creates_uuid(mci_env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)

    out = h.handler({"customer_keys": [{"tenant_id": "t1", "customer_id": "ext1"}]})

    assert "t1" in out
    assert "ext1" in out["t1"]
    assert out["t1"]["ext1"]  # a UUID string


@mock_aws
def test_existing_mapping_is_stable(mci_env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)

    event = {"customer_keys": [{"tenant_id": "t1", "customer_id": "ext1"}]}
    first = h.handler(event)["t1"]["ext1"]
    second = h.handler(event)["t1"]["ext1"]

    assert first == second  # same external id always resolves to same uuid


@mock_aws
def test_deduplication(mci_env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)

    # Same key three times in one request.
    event = {
        "customer_keys": [
            {"tenant_id": "t1", "customer_id": "dup"},
            {"tenant_id": "t1", "customer_id": "dup"},
            {"tenant_id": "t1", "customer_id": "dup"},
        ]
    }
    out = h.handler(event)
    assert out == {"t1": {"dup": out["t1"]["dup"]}}  # collapsed to one entry


@mock_aws
def test_read_only_raises_for_missing(mci_env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)

    event = {
        "customer_keys": [{"tenant_id": "t1", "customer_id": "never-seen"}],
        "read_only": True,
    }
    with pytest.raises(KeyError):
        h.handler(event)


@mock_aws
def test_multi_tenant_output_shape(mci_env):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)

    event = {
        "customer_keys": [
            {"tenant_id": "t1", "customer_id": "a"},
            {"tenant_id": "t1", "customer_id": "b"},
            {"tenant_id": "t2", "customer_id": "a"},
        ]
    }
    out = h.handler(event)

    assert set(out.keys()) == {"t1", "t2"}
    assert set(out["t1"].keys()) == {"a", "b"}
    assert set(out["t2"].keys()) == {"a"}
    # t1#a and t2#a are different keys, so different UUIDs
    assert out["t1"]["a"] != out["t2"]["a"]
