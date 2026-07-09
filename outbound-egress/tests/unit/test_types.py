"""Tests for outbound_egress.model.types.

These aren't in the spec's Testing Requirements list (that section starts
at the JQ/webhook/kinesis/oauth/KMS layer), but round-tripping the DynamoDB
item shapes and the destination tagged-union dispatch here catches schema
mistakes before they surface as confusing failures three commits from now
in the store or delivery layers.
"""

from outbound_egress.model.types import (
    DESTINATION_JWT_OAUTH,
    DESTINATION_KINESIS,
    DESTINATION_WEBHOOK,
    BatchProgress,
    ConnectionStatus,
    Connector,
    IntegrationMetadata,
    JwtOAuthDestination,
    KinesisDestination,
    Transformation,
    WebhookDestination,
    parse_destination,
)


def test_connector_to_item_from_item_round_trip_plain_webhook():
    connector = Connector(
        tenant_id="tenant-abc",
        name="RetailBrandWebhook",
        payload_type="ACHIEVED",
        destination_type=DESTINATION_WEBHOOK,
        destination="ciphertext-blob",
        transformation_name="mparticle-s2s",
        connection_status=ConnectionStatus(status_code=200, timestamp="2026-07-06T12:00:00+00:00"),
    )

    item = connector.to_item()
    assert item["partition_key"] == "tenant-abc"
    assert item["sort_key"] == "RetailBrandWebhook"
    assert "integration" not in item

    restored = Connector.from_item(item)
    assert restored == connector
    assert restored.is_integration is False


def test_connector_is_integration_when_integration_attribute_present():
    connector = Connector(
        tenant_id="tenant-abc",
        name="MyMParticleIntegration-ACTIVATED",
        payload_type="ACTIVATED",
        destination_type=DESTINATION_WEBHOOK,
        destination="ciphertext-blob",
        integration=IntegrationMetadata(
            integration_name="MyMParticleIntegration",
            integration_type="mparticle",
            environment="prod",
            encrypted_credentials="cred-ciphertext",
        ),
    )

    item = connector.to_item()
    assert item["integration"]["integration_type"] == "mparticle"

    restored = Connector.from_item(item)
    assert restored.is_integration is True


def test_parse_destination_dispatches_webhook():
    dest = parse_destination(
        DESTINATION_WEBHOOK,
        {"url": "https://example.com/hook", "method": "POST", "headers": {"X-Api-Key": "k"}},
    )
    assert isinstance(dest, WebhookDestination)
    assert dest.url == "https://example.com/hook"


def test_parse_destination_dispatches_kinesis():
    dest = parse_destination(
        DESTINATION_KINESIS,
        {
            "account_id": "111122223333",
            "role_name": "cross-account-role",
            "stream_name": "brand-events",
            "region": "us-east-2",
        },
    )
    assert isinstance(dest, KinesisDestination)
    assert dest.account_id == "111122223333"


def test_parse_destination_dispatches_jwt_oauth():
    dest = parse_destination(
        DESTINATION_JWT_OAUTH,
        {
            "token_url": "https://auth.example.com/token",
            "destination_url": "https://api.example.com/events",
            "signing_key_pem": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
            "jwt_issuer": "seed-platform",
            "jwt_audience": "example-brand",
        },
    )
    assert isinstance(dest, JwtOAuthDestination)
    assert dest.jwt_algorithm == "RS256"


def test_parse_destination_unknown_type_raises():
    import pytest

    with pytest.raises(ValueError, match="unknown destination_type"):
        parse_destination("carrier_pigeon", {})


def test_transformation_round_trip():
    transformation = Transformation(
        tenant_id="tenant-abc",
        name="mparticle-s2s",
        jq_script="{event: .offer_id}",
        description="Reshapes progress payload into mParticle S2S format",
    )
    item = transformation.to_item()
    assert item["partition_key"] == "tenant-abc"
    assert item["sort_key"] == "mparticle-s2s"

    restored = Transformation.from_item(item)
    assert restored == transformation


def test_batch_progress_from_dict_parses_multiple_progresses():
    raw = {
        "trace": {"trace_id": "abc-123"},
        "edge_time": "2026-07-06T12:00:00+00:00",
        "progresses": [
            {
                "tenant_id": "tenant-abc",
                "internal_customer_id": "uuid-1",
                "offer_id": "offer-1",
                "campaign_id": "campaign-1",
                "connectors": ["RetailBrandWebhook"],
                "status": "ACHIEVED",
                "all_outcomes": ["ACTIVATED", "PROGRESSED", "ACHIEVED"],
            },
            {
                "tenant_id": "tenant-abc",
                "internal_customer_id": "uuid-2",
                "offer_id": "offer-2",
                "campaign_id": "campaign-1",
                "connectors": [],
                "status": "READY",
            },
        ],
    }

    batch = BatchProgress.from_dict(raw)
    assert batch.trace["trace_id"] == "abc-123"
    assert len(batch.progresses) == 2
    assert batch.progresses[0].connectors == ["RetailBrandWebhook"]
    assert batch.progresses[0].all_outcomes == ["ACTIVATED", "PROGRESSED", "ACHIEVED"]
    assert batch.progresses[1].connectors == []
