"""Tests for outbound_egress.handlers.aqueduct_writer."""

import boto3
import pytest
from moto import mock_aws

import outbound_egress.handlers.aqueduct_writer as writer
from outbound_egress.model.types import DESTINATION_WEBHOOK
from outbound_egress.store.connectors import ConnectorStore

CONNECTORS_TABLE = "test-connectors"


@pytest.fixture
def wired(monkeypatch):
    """Wires the writer module's store and crypto to a moto-backed table
    and KMS key, matching event_ingress's monkeypatch.setattr pattern."""
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
        kms_client = boto3.client("kms", region_name="us-east-2")
        key_id = kms_client.create_key(Description="test")["KeyMetadata"]["KeyId"]

        from outbound_egress.crypto.kms import DestinationCrypto

        store = ConnectorStore(CONNECTORS_TABLE, dynamodb_resource=dynamodb_resource)
        crypto = DestinationCrypto(kms_client=kms_client)

        monkeypatch.setattr(writer, "_connector_store", store)
        monkeypatch.setattr(writer, "_destination_crypto", crypto)
        monkeypatch.setattr(writer, "CONNECTOR_KMS_KEY_ID", key_id)

        yield store


def test_create_connector_stores_encrypted_destination(wired):
    result = writer.handler(
        {
            "operation": "CreateConnector",
            "input": {
                "tenant_id": "tenant-abc",
                "name": "RetailBrandWebhook",
                "payload_type": "ACHIEVED",
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://example.com/hook", "method": "POST"},
            },
        }
    )

    assert result["name"] == "RetailBrandWebhook"
    assert result["is_integration"] is False
    assert "destination" not in result  # never echoed back

    stored = wired.get_connectors("tenant-abc", ["RetailBrandWebhook"])["RetailBrandWebhook"]
    assert stored.destination != {"url": "https://example.com/hook"}  # it's ciphertext


def test_create_integration_with_three_event_types_produces_three_rows(wired):
    result = writer.handler(
        {
            "operation": "CreateIntegration",
            "input": {
                "tenant_id": "tenant-abc",
                "integration_name": "MyMParticleIntegration",
                "integration_type": "mparticle",
                "environment": "prod",
                "event_types": ["READY", "ACTIVATED", "ACHIEVED"],
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://mparticle.example.com/hook"},
                "credentials": {"api_key": "secret"},
            },
        }
    )

    assert len(result) == 3
    names = {row["name"] for row in result}
    assert names == {
        "MyMParticleIntegration-READY",
        "MyMParticleIntegration-ACTIVATED",
        "MyMParticleIntegration-ACHIEVED",
    }
    assert all(row["is_integration"] for row in result)
    assert all(row["integration_name"] == "MyMParticleIntegration" for row in result)

    integrations = wired.list_integrations("tenant-abc")
    assert len(integrations) == 3


def test_delete_integration_removes_all_associated_connector_rows(wired):
    writer.handler(
        {
            "operation": "CreateIntegration",
            "input": {
                "tenant_id": "tenant-abc",
                "integration_name": "MyMParticleIntegration",
                "integration_type": "mparticle",
                "environment": "prod",
                "event_types": ["READY", "ACTIVATED", "ACHIEVED"],
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://mparticle.example.com/hook"},
            },
        }
    )
    # A plain webhook, unrelated to the integration, must survive.
    writer.handler(
        {
            "operation": "CreateConnector",
            "input": {
                "tenant_id": "tenant-abc",
                "name": "UnrelatedWebhook",
                "payload_type": "ACHIEVED",
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://unrelated.example.com/hook"},
            },
        }
    )

    result = writer.handler(
        {
            "operation": "DeleteIntegration",
            "input": {"tenant_id": "tenant-abc", "integration_name": "MyMParticleIntegration"},
        }
    )

    assert set(result["deleted"]) == {
        "MyMParticleIntegration-READY",
        "MyMParticleIntegration-ACTIVATED",
        "MyMParticleIntegration-ACHIEVED",
    }
    assert wired.list_integrations("tenant-abc") == []
    assert wired.get_connectors("tenant-abc", ["UnrelatedWebhook"])  # still there


def test_create_integration_rejects_unknown_offer_state(wired):
    with pytest.raises(ValueError, match="unknown offer state"):
        writer.handler(
            {
                "operation": "CreateIntegration",
                "input": {
                    "tenant_id": "tenant-abc",
                    "integration_name": "Bad",
                    "integration_type": "mparticle",
                    "environment": "prod",
                    "event_types": ["NOT_A_REAL_STATE"],
                    "destination_type": DESTINATION_WEBHOOK,
                    "destination": {"url": "https://example.com"},
                },
            }
        )


def test_create_integration_rejects_empty_event_types(wired):
    with pytest.raises(ValueError, match="at least one event type"):
        writer.handler(
            {
                "operation": "CreateIntegration",
                "input": {
                    "tenant_id": "tenant-abc",
                    "integration_name": "Bad",
                    "integration_type": "mparticle",
                    "environment": "prod",
                    "event_types": [],
                    "destination_type": DESTINATION_WEBHOOK,
                    "destination": {"url": "https://example.com"},
                },
            }
        )


def test_update_connector_toggles_enabled_and_preserves_connection_status(wired):
    writer.handler(
        {
            "operation": "CreateConnector",
            "input": {
                "tenant_id": "tenant-abc",
                "name": "RetailBrandWebhook",
                "payload_type": "ACHIEVED",
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://example.com/hook"},
            },
        }
    )

    from outbound_egress.model.types import ConnectionStatus

    wired.update_connection_status(
        "tenant-abc",
        "RetailBrandWebhook",
        ConnectionStatus(status_code=200, timestamp="2026-07-06T12:00:00+00:00"),
    )

    result = writer.handler(
        {
            "operation": "UpdateConnector",
            "input": {"tenant_id": "tenant-abc", "name": "RetailBrandWebhook", "enabled": False},
        }
    )

    assert result["enabled"] is False
    assert result["connection_status"]["status_code"] == 200  # untouched by the update


def test_update_connector_missing_row_raises_key_error(wired):
    with pytest.raises(KeyError):
        writer.handler(
            {
                "operation": "UpdateConnector",
                "input": {"tenant_id": "tenant-abc", "name": "DoesNotExist", "enabled": False},
            }
        )


def test_delete_connector_returns_true_then_false(wired):
    writer.handler(
        {
            "operation": "CreateConnector",
            "input": {
                "tenant_id": "tenant-abc",
                "name": "RetailBrandWebhook",
                "payload_type": "ACHIEVED",
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://example.com/hook"},
            },
        }
    )

    first = writer.handler(
        {
            "operation": "DeleteConnector",
            "input": {"tenant_id": "tenant-abc", "name": "RetailBrandWebhook"},
        }
    )
    second = writer.handler(
        {
            "operation": "DeleteConnector",
            "input": {"tenant_id": "tenant-abc", "name": "RetailBrandWebhook"},
        }
    )

    assert first == {"deleted": True}
    assert second == {"deleted": False}


def test_list_connectors_excludes_integration_rows(wired):
    writer.handler(
        {
            "operation": "CreateConnector",
            "input": {
                "tenant_id": "tenant-abc",
                "name": "PlainWebhook",
                "payload_type": "ACHIEVED",
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://example.com/hook"},
            },
        }
    )
    writer.handler(
        {
            "operation": "CreateIntegration",
            "input": {
                "tenant_id": "tenant-abc",
                "integration_name": "MyIntegration",
                "integration_type": "mparticle",
                "environment": "prod",
                "event_types": ["ACHIEVED"],
                "destination_type": DESTINATION_WEBHOOK,
                "destination": {"url": "https://example.com/hook"},
            },
        }
    )

    plain = writer.handler({"operation": "ListConnectors", "input": {"tenant_id": "tenant-abc"}})

    assert [c["name"] for c in plain] == ["PlainWebhook"]


def test_unknown_operation_raises_value_error(wired):
    with pytest.raises(ValueError, match="unknown operation"):
        writer.handler({"operation": "DoSomethingElse", "input": {}})
