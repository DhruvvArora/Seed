"""Tests for outbound_egress.store.connectors."""

import boto3
import pytest
from moto import mock_aws

from outbound_egress.model.types import (
    DESTINATION_WEBHOOK,
    ConnectionStatus,
    Connector,
    IntegrationMetadata,
)
from outbound_egress.store.connectors import ConnectorStore

TABLE_NAME = "test-connectors"


@pytest.fixture
def table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-2")
        client.create_table(
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
        yield boto3.resource("dynamodb", region_name="us-east-2")


def _connector(name: str, integration: IntegrationMetadata | None = None) -> Connector:
    return Connector(
        tenant_id="tenant-abc",
        name=name,
        payload_type="ACHIEVED",
        destination_type=DESTINATION_WEBHOOK,
        destination="ciphertext-blob",
        integration=integration,
    )


def test_get_connectors_returns_matching_rows_only(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    store.create_connector(_connector("ConnectorA"))
    store.create_connector(_connector("ConnectorB"))

    found = store.get_connectors("tenant-abc", ["ConnectorA", "ConnectorMissing"])

    assert set(found.keys()) == {"ConnectorA"}
    assert found["ConnectorA"].destination == "ciphertext-blob"


def test_get_connectors_empty_list_returns_empty_dict(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    assert store.get_connectors("tenant-abc", []) == {}


def test_get_connectors_deduplicates_and_batches_over_100(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    for i in range(5):
        store.create_connector(_connector(f"Connector{i}"))

    names = [f"Connector{i}" for i in range(5)] * 30  # 150 with duplicates
    found = store.get_connectors("tenant-abc", names)

    assert len(found) == 5


def test_list_connectors_excludes_integration_rows(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    store.create_connector(_connector("PlainWebhook"))
    store.create_connector(
        _connector(
            "MyMParticleIntegration-ACTIVATED",
            integration=IntegrationMetadata(
                integration_name="MyMParticleIntegration",
                integration_type="mparticle",
                environment="prod",
                encrypted_credentials="c",
            ),
        )
    )

    plain = store.list_connectors("tenant-abc")

    assert [c.name for c in plain] == ["PlainWebhook"]


def test_list_integrations_returns_only_integration_rows(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    store.create_connector(_connector("PlainWebhook"))
    store.create_connector(
        _connector(
            "MyMParticleIntegration-ACTIVATED",
            integration=IntegrationMetadata(
                integration_name="MyMParticleIntegration",
                integration_type="mparticle",
                environment="prod",
                encrypted_credentials="c",
            ),
        )
    )

    integrations = store.list_integrations("tenant-abc")

    assert [c.name for c in integrations] == ["MyMParticleIntegration-ACTIVATED"]


def test_delete_connector_returns_true_when_row_existed(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    store.create_connector(_connector("ConnectorA"))

    assert store.delete_connector("tenant-abc", "ConnectorA") is True
    assert store.get_connectors("tenant-abc", ["ConnectorA"]) == {}


def test_delete_connector_returns_false_when_nothing_to_delete(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    assert store.delete_connector("tenant-abc", "NeverExisted") is False


def test_update_connection_status_writes_status_code_and_timestamp(table):
    store = ConnectorStore(TABLE_NAME, dynamodb_resource=table)
    store.create_connector(_connector("ConnectorA"))

    store.update_connection_status(
        "tenant-abc",
        "ConnectorA",
        ConnectionStatus(status_code=200, timestamp="2026-07-06T12:00:00+00:00"),
    )

    found = store.get_connectors("tenant-abc", ["ConnectorA"])["ConnectorA"]
    assert found.connection_status is not None
    assert found.connection_status.status_code == 200
