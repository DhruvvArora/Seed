"""End-to-end tests for outbound_egress.handlers.aqueduct_distributor.

Exercises the whole path: decode a Kinesis record, batch-load connectors,
decrypt destinations (through the invocation-scoped DecryptCache), deliver
via webhook (mocked with `responses`), and write connection_status back to
DynamoDB (moto). DynamoDB, KMS, and the module-level store/crypto singletons
are wired up via monkeypatch, matching the pattern used in
event_ingress.tests.unit.test_event_enqueue (monkeypatch.setattr on the
handler module's globals) rather than real environment variables.

IMPORTANT on mock ordering: `responses` must be entered AFTER `mock_aws()`,
as an inner context manager, not as an outer `@responses.activate` decorator
wrapping a nested `with mock_aws():` block. Verified locally: with `mock_aws`
outside and `responses` applied via the decorator on the outer test function,
webhook deliveries still succeed (the mock still intercepts and returns the
registered response) but `responses.calls` silently stays empty, so any test
asserting call counts passes or fails for the wrong reason. Nesting
`responses.RequestsMock()` inside `with mock_aws():` fixes it.
"""

import base64
import json

import boto3
import responses as responses_module
from moto import mock_aws

import outbound_egress.handlers.aqueduct_distributor as distributor
from outbound_egress.crypto.kms import DestinationCrypto
from outbound_egress.model.types import DESTINATION_WEBHOOK, Connector
from outbound_egress.store.connectors import ConnectorStore
from outbound_egress.store.transformations import TransformationStore

CONNECTORS_TABLE = "test-connectors"
TRANSFORMATIONS_TABLE = "test-transformations"


def _create_tables():
    client = boto3.client("dynamodb", region_name="us-east-2")
    for table_name in (CONNECTORS_TABLE, TRANSFORMATIONS_TABLE):
        client.create_table(
            TableName=table_name,
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


def _kinesis_record(batch_dict: dict) -> dict:
    raw_json = json.dumps(batch_dict).encode("utf-8")
    return {"kinesis": {"data": base64.b64encode(raw_json).decode("ascii")}}


class _CountingCrypto(DestinationCrypto):
    """Wraps DestinationCrypto to count real decrypt calls, so tests can
    assert the DecryptCache is actually preventing redundant KMS calls."""

    def __init__(self, kms_client=None):
        super().__init__(kms_client=kms_client)
        self.decrypt_call_count = 0

    def decrypt(self, ciphertext_b64: str) -> dict:
        self.decrypt_call_count += 1
        return super().decrypt(ciphertext_b64)


def test_distributor_fans_out_within_a_progress_and_reuses_cache_across_progresses(monkeypatch):
    with mock_aws():
        _create_tables()
        dynamodb_resource = boto3.resource("dynamodb", region_name="us-east-2")
        kms_client = boto3.client("kms", region_name="us-east-2")
        kms_key_id = kms_client.create_key(Description="test")["KeyMetadata"]["KeyId"]

        crypto = _CountingCrypto(kms_client=kms_client)
        ciphertext_a = crypto.encrypt(kms_key_id, {"url": "https://a.example.com/hook"})
        ciphertext_b = crypto.encrypt(kms_key_id, {"url": "https://b.example.com/hook"})

        connector_store = ConnectorStore(CONNECTORS_TABLE, dynamodb_resource=dynamodb_resource)
        connector_store.create_connector(
            Connector(
                tenant_id="tenant-abc",
                name="ConnectorA",
                payload_type="ACHIEVED",
                destination_type=DESTINATION_WEBHOOK,
                destination=ciphertext_a,
            )
        )
        connector_store.create_connector(
            Connector(
                tenant_id="tenant-abc",
                name="ConnectorB",
                payload_type="ACHIEVED",
                destination_type=DESTINATION_WEBHOOK,
                destination=ciphertext_b,
            )
        )

        transformation_store = TransformationStore(
            TRANSFORMATIONS_TABLE, dynamodb_resource=dynamodb_resource
        )

        monkeypatch.setattr(distributor, "_connector_store", connector_store)
        monkeypatch.setattr(distributor, "_transformation_store", transformation_store)
        monkeypatch.setattr(distributor, "_destination_crypto", crypto)

        batch = {
            "trace": {"trace_id": "abc-123"},
            "edge_time": "2026-07-06T12:00:00+00:00",
            "progresses": [
                {
                    "tenant_id": "tenant-abc",
                    "internal_customer_id": "uuid-1",
                    "offer_id": "offer-1",
                    "campaign_id": "campaign-1",
                    "connectors": ["ConnectorA", "ConnectorB"],
                    "status": "ACHIEVED",
                },
                {
                    "tenant_id": "tenant-abc",
                    "internal_customer_id": "uuid-2",
                    "offer_id": "offer-2",
                    "campaign_id": "campaign-1",
                    "connectors": ["ConnectorA"],
                    "status": "ACHIEVED",
                },
            ],
        }
        event = {"Records": [_kinesis_record(batch)]}

        with responses_module.RequestsMock() as rsps:
            rsps.add(responses_module.POST, "https://a.example.com/hook", status=200)
            rsps.add(responses_module.POST, "https://b.example.com/hook", status=200)
            rsps.add(responses_module.POST, "https://a.example.com/hook", status=200)

            distributor.handler(event, None)

            # 3 deliveries happened (A, B for progress 1; A again for progress 2)...
            assert len(rsps.calls) == 3

        # ...but ConnectorA and ConnectorB's destinations were each decrypted
        # only once, since the DecryptCache is shared across both progress
        # events within this one invocation.
        assert crypto.decrypt_call_count == 2

        connector_a = connector_store.get_connectors("tenant-abc", ["ConnectorA"])["ConnectorA"]
        connector_b = connector_store.get_connectors("tenant-abc", ["ConnectorB"])["ConnectorB"]
        assert connector_a.connection_status is not None
        assert connector_a.connection_status.status_code == 200
        assert connector_b.connection_status is not None
        assert connector_b.connection_status.status_code == 200


def test_distributor_writes_failure_status_and_continues_other_connectors(monkeypatch):
    with mock_aws():
        _create_tables()
        dynamodb_resource = boto3.resource("dynamodb", region_name="us-east-2")
        kms_client = boto3.client("kms", region_name="us-east-2")
        kms_key_id = kms_client.create_key(Description="test")["KeyMetadata"]["KeyId"]

        crypto = DestinationCrypto(kms_client=kms_client)
        ciphertext_good = crypto.encrypt(kms_key_id, {"url": "https://good.example.com/hook"})
        ciphertext_bad = crypto.encrypt(kms_key_id, {"url": "https://bad.example.com/hook"})

        connector_store = ConnectorStore(CONNECTORS_TABLE, dynamodb_resource=dynamodb_resource)
        connector_store.create_connector(
            Connector(
                tenant_id="tenant-abc",
                name="GoodConnector",
                payload_type="ACHIEVED",
                destination_type=DESTINATION_WEBHOOK,
                destination=ciphertext_good,
            )
        )
        connector_store.create_connector(
            Connector(
                tenant_id="tenant-abc",
                name="BadConnector",
                payload_type="ACHIEVED",
                destination_type=DESTINATION_WEBHOOK,
                destination=ciphertext_bad,
            )
        )

        transformation_store = TransformationStore(
            TRANSFORMATIONS_TABLE, dynamodb_resource=dynamodb_resource
        )

        monkeypatch.setattr(distributor, "_connector_store", connector_store)
        monkeypatch.setattr(distributor, "_transformation_store", transformation_store)
        monkeypatch.setattr(distributor, "_destination_crypto", crypto)
        # Keep the test fast: no need to actually sleep between retries here.
        monkeypatch.setattr(distributor, "RETRY_BACKOFF_SECONDS", 0.0)

        batch = {
            "edge_time": "2026-07-06T12:00:00+00:00",
            "progresses": [
                {
                    "tenant_id": "tenant-abc",
                    "internal_customer_id": "uuid-1",
                    "offer_id": "offer-1",
                    "campaign_id": "campaign-1",
                    "connectors": ["GoodConnector", "BadConnector"],
                    "status": "ACHIEVED",
                }
            ],
        }
        event = {"Records": [_kinesis_record(batch)]}

        with responses_module.RequestsMock() as rsps:
            rsps.add(responses_module.POST, "https://good.example.com/hook", status=200)
            rsps.add(responses_module.POST, "https://bad.example.com/hook", status=400)

            # Must not raise: BadConnector's failure is isolated.
            distributor.handler(event, None)

        good = connector_store.get_connectors("tenant-abc", ["GoodConnector"])["GoodConnector"]
        bad = connector_store.get_connectors("tenant-abc", ["BadConnector"])["BadConnector"]
        assert good.connection_status is not None
        assert good.connection_status.status_code == 200
        assert bad.connection_status is not None
        assert bad.connection_status.status_code == 400
