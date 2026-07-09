"""aqueduct-writer: CRUD API for connector management.

Invoked by the GraphQL API layer via Lambda Invoke, not directly by
clients and not through API Gateway. Dispatches on an "operation" field:

  CreateConnector   -- create a single connector row (webhook, kinesis, or
                       jwt_oauth). destination is KMS-encrypted before storage.
  CreateIntegration -- fan out one integration into multiple connector rows,
                       one per offer state named in event_types (e.g. an
                       mParticle integration covering READY, ACTIVATED,
                       ACHIEVED becomes 3 rows: "{name}-READY",
                       "{name}-ACTIVATED", "{name}-ACHIEVED"). Both the shared
                       destination and the integration credentials are
                       KMS-encrypted before storage.
  UpdateConnector   -- update an existing row's destination (re-encrypted if
                       supplied), transformation_name, or enabled flag.
                       connection_status is left untouched.
  DeleteConnector   -- remove a single connector row by tenant_id and name.
  DeleteIntegration -- remove every connector row belonging to one
                       integration (all its "{name}-{STATE}" rows), not just
                       one of them. Distinct from DeleteConnector.
  ListConnectors    -- rows where the integration attribute is absent (plain
                       webhooks only).
  ListIntegrations  -- rows where the integration attribute is present.

This Lambda never decrypts a destination; it only ever encrypts on write.
Reads (List*) return a summary that omits the encrypted destination blob,
since GraphQL callers manage destinations through the write operations and
never need it echoed back.
"""

from __future__ import annotations

import os
from typing import Any

from outbound_egress.crypto.kms import DestinationCrypto
from outbound_egress.model.types import Connector, IntegrationMetadata
from outbound_egress.store.connectors import ConnectorStore

CONNECTORS_TABLE_NAME = os.environ.get("CONNECTORS_TABLE_NAME", "")
CONNECTOR_KMS_KEY_ID = os.environ.get("CONNECTOR_KMS_KEY_ID", "")

VALID_OFFER_STATES = {
    "ASSIGNED",
    "READY",
    "ACTIVATED",
    "PROGRESSED",
    "ACHIEVED",
    "COMPLETED",
    "REWARD_EARNED",
}

_connector_store: ConnectorStore | None = None
_destination_crypto: DestinationCrypto | None = None


def _get_connector_store() -> ConnectorStore:
    global _connector_store
    if _connector_store is None:
        _connector_store = ConnectorStore(CONNECTORS_TABLE_NAME)
    return _connector_store


def _get_destination_crypto() -> DestinationCrypto:
    global _destination_crypto
    if _destination_crypto is None:
        _destination_crypto = DestinationCrypto()
    return _destination_crypto


def _create_connector(input_data: dict[str, Any]) -> dict[str, Any]:
    ciphertext = _get_destination_crypto().encrypt(CONNECTOR_KMS_KEY_ID, input_data["destination"])
    connector = Connector(
        tenant_id=input_data["tenant_id"],
        name=input_data["name"],
        payload_type=input_data["payload_type"],
        destination_type=input_data["destination_type"],
        destination=ciphertext,
        transformation_name=input_data.get("transformation_name"),
        enabled=input_data.get("enabled", True),
    )
    _get_connector_store().create_connector(connector)
    return connector.to_summary_dict()


def _create_integration(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    event_types = input_data["event_types"]
    if not event_types:
        raise ValueError("CreateIntegration requires at least one event type")
    unknown = [s for s in event_types if s not in VALID_OFFER_STATES]
    if unknown:
        raise ValueError(f"unknown offer state(s) in event_types: {unknown}")

    crypto = _get_destination_crypto()
    destination_ciphertext = crypto.encrypt(CONNECTOR_KMS_KEY_ID, input_data["destination"])
    credentials_ciphertext = crypto.encrypt(CONNECTOR_KMS_KEY_ID, input_data.get("credentials", {}))

    integration = IntegrationMetadata(
        integration_name=input_data["integration_name"],
        integration_type=input_data["integration_type"],
        environment=input_data["environment"],
        encrypted_credentials=credentials_ciphertext,
    )

    store = _get_connector_store()
    tenant_id = input_data["tenant_id"]
    integration_name = input_data["integration_name"]

    created: list[dict[str, Any]] = []
    for state in event_types:
        connector = Connector(
            tenant_id=tenant_id,
            name=f"{integration_name}-{state}",
            payload_type=state,
            destination_type=input_data["destination_type"],
            destination=destination_ciphertext,
            transformation_name=input_data.get("transformation_name"),
            integration=integration,
        )
        store.create_connector(connector)
        created.append(connector.to_summary_dict())

    return created


def _update_connector(input_data: dict[str, Any]) -> dict[str, Any]:
    tenant_id = input_data["tenant_id"]
    name = input_data["name"]
    store = _get_connector_store()

    existing = store.get_connectors(tenant_id, [name]).get(name)
    if existing is None:
        raise KeyError(f"no connector named {name!r} for tenant {tenant_id!r}")

    if "destination" in input_data:
        existing.destination = _get_destination_crypto().encrypt(
            CONNECTOR_KMS_KEY_ID, input_data["destination"]
        )
    if "transformation_name" in input_data:
        existing.transformation_name = input_data["transformation_name"]
    if "enabled" in input_data:
        existing.enabled = input_data["enabled"]

    store.create_connector(existing)  # put_item overwrite of the same key
    return existing.to_summary_dict()


def _delete_connector(input_data: dict[str, Any]) -> dict[str, Any]:
    deleted = _get_connector_store().delete_connector(input_data["tenant_id"], input_data["name"])
    return {"deleted": deleted}


def _delete_integration(input_data: dict[str, Any]) -> dict[str, Any]:
    deleted_names = _get_connector_store().delete_integration(
        input_data["tenant_id"], input_data["integration_name"]
    )
    return {"deleted": deleted_names}


def _list_connectors(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    connectors = _get_connector_store().list_connectors(input_data["tenant_id"])
    return [c.to_summary_dict() for c in connectors]


def _list_integrations(input_data: dict[str, Any]) -> list[dict[str, Any]]:
    connectors = _get_connector_store().list_integrations(input_data["tenant_id"])
    return [c.to_summary_dict() for c in connectors]


_OPERATIONS = {
    "CreateConnector": _create_connector,
    "CreateIntegration": _create_integration,
    "UpdateConnector": _update_connector,
    "DeleteConnector": _delete_connector,
    "DeleteIntegration": _delete_integration,
    "ListConnectors": _list_connectors,
    "ListIntegrations": _list_integrations,
}


def handler(event: dict[str, Any], context: Any = None) -> Any:
    operation = event.get("operation")
    if operation not in _OPERATIONS:
        raise ValueError(f"unknown operation: {operation!r}")
    input_data = event.get("input", {})
    return _OPERATIONS[operation](input_data)
