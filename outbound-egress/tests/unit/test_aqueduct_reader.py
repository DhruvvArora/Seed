"""Tests for outbound_egress.handlers.aqueduct_reader."""

import boto3
import pytest
from moto import mock_aws

import outbound_egress.handlers.aqueduct_reader as reader
from outbound_egress.model.types import DESTINATION_WEBHOOK, Connector
from outbound_egress.store.connectors import ConnectorStore

CONNECTORS_TABLE = "test-connectors"


@pytest.fixture
def wired(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-2")
        client.create_table(
            TableName=CONNECTORS_TABLE,
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
        dynamodb_resource = boto3.resource("dynamodb", region_name="us-east-2")
        store = ConnectorStore(CONNECTORS_TABLE, dynamodb_resource=dynamodb_resource)

        store.create_connector(
            Connector(
                tenant_id="tenant-abc",
                name="RetailBrandWebhook",
                payload_type="ACHIEVED",
                destination_type=DESTINATION_WEBHOOK,
                destination="ciphertext-blob",
            )
        )

        monkeypatch.setattr(reader, "_connector_store", store)
        yield store


def test_reader_returns_summary_for_found_connector(wired):
    result = reader.handler({"tenant_id": "tenant-abc", "connector_names": ["RetailBrandWebhook"]})

    assert result["RetailBrandWebhook"]["name"] == "RetailBrandWebhook"
    assert "destination" not in result["RetailBrandWebhook"]


def test_reader_returns_none_for_missing_connector(wired):
    result = reader.handler(
        {"tenant_id": "tenant-abc", "connector_names": ["RetailBrandWebhook", "DoesNotExist"]}
    )

    assert result["RetailBrandWebhook"] is not None
    assert result["DoesNotExist"] is None


def test_reader_returns_empty_dict_for_no_names(wired):
    result = reader.handler({"tenant_id": "tenant-abc", "connector_names": []})
    assert result == {}
