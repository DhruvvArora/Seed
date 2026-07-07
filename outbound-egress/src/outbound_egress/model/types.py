"""Typed structures for the Outbound Connector Engine.

Three shapes are modelled here:

  1. Connector -- one row per connector in the connectors DynamoDB table.
     `destination` is the encrypted (base64 ciphertext) blob as stored; the
     crypto layer decrypts it into one of the Destination variants below at
     delivery time. `integration` is present only when this row belongs to a
     multi-event-type integration (e.g. an mParticle integration spanning
     READY/ACTIVATED/ACHIEVED) rather than a plain webhook -- this is exactly
     how ListConnectors and ListIntegrations tell rows apart.

  2. Destination -- a tagged union (WebhookDestination / KinesisDestination /
     JwtOAuthDestination). The connector's `destination_type` field says which
     variant to parse the decrypted JSON into.

  3. BatchProgress / Progress -- the message shape carried on the plague
     Kinesis stream. One BatchProgress can hold multiple Progress entries for
     multiple customers; each Progress's `connectors` list says exactly which
     connector rows the distributor must load and invoke for that event.

Transformation models a row in the transformations table (the JQ script that
reshapes a progress payload for a specific connector).

DeliveryResult is the common return shape for all three delivery paths
(webhook, kinesis, oauth), so aqueduct-distributor can write connection_status
the same way regardless of destination type.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# destination_type values
DESTINATION_WEBHOOK = "webhook"
DESTINATION_KINESIS = "kinesis"
DESTINATION_JWT_OAUTH = "jwt_oauth"

# HTTP methods used by webhook and jwt_oauth destinations
METHOD_POST = "POST"
METHOD_PUT = "PUT"

# Offer lifecycle states (see readme.md). payload_type on a connector row is
# one of these -- which transition the tenant wants delivered to this
# connector.
STATE_ASSIGNED = "ASSIGNED"
STATE_READY = "READY"
STATE_ACTIVATED = "ACTIVATED"
STATE_PROGRESSED = "PROGRESSED"
STATE_ACHIEVED = "ACHIEVED"
STATE_COMPLETED = "COMPLETED"
STATE_REWARD_EARNED = "REWARD_EARNED"


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp, e.g. '2026-07-06T18:30:00+00:00'."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ConnectionStatus:
    """Last delivery result for a connector: HTTP status code and timestamp."""

    status_code: int
    timestamp: str

    def to_item(self) -> dict[str, Any]:
        return {"status_code": self.status_code, "timestamp": self.timestamp}

    @staticmethod
    def from_item(raw: dict[str, Any] | None) -> ConnectionStatus | None:
        if not raw:
            return None
        return ConnectionStatus(status_code=int(raw["status_code"]), timestamp=raw["timestamp"])


@dataclass(frozen=True)
class IntegrationMetadata:
    """Marks a connector row as belonging to a multi-event-type integration.

    Present only on rows created via CreateIntegration (e.g. one mParticle
    integration fanning out into 3-6 connector rows, one per offer state).
    Absent on plain webhook rows created via CreateConnector. This is the
    single source of truth ListConnectors and ListIntegrations filter on.
    """

    integration_type: str
    environment: str
    encrypted_credentials: str

    def to_item(self) -> dict[str, Any]:
        return {
            "integration_type": self.integration_type,
            "environment": self.environment,
            "encrypted_credentials": self.encrypted_credentials,
        }

    @staticmethod
    def from_item(raw: dict[str, Any] | None) -> IntegrationMetadata | None:
        if not raw:
            return None
        return IntegrationMetadata(
            integration_type=raw["integration_type"],
            environment=raw["environment"],
            encrypted_credentials=raw["encrypted_credentials"],
        )


@dataclass(frozen=True)
class WebhookDestination:
    """Plain webhook. Credentials belong in headers, never in plain text elsewhere."""

    url: str
    method: str = METHOD_POST
    headers: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "method": self.method, "headers": dict(self.headers)}

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> WebhookDestination:
        return WebhookDestination(
            url=raw["url"],
            method=raw.get("method", METHOD_POST),
            headers=dict(raw.get("headers", {})),
        )


@dataclass(frozen=True)
class KinesisDestination:
    """Cross-account Kinesis. account_id/role_name drive the STS AssumeRole call."""

    account_id: str
    role_name: str
    stream_name: str
    region: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "role_name": self.role_name,
            "stream_name": self.stream_name,
            "region": self.region,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> KinesisDestination:
        return KinesisDestination(
            account_id=raw["account_id"],
            role_name=raw["role_name"],
            stream_name=raw["stream_name"],
            region=raw["region"],
        )


@dataclass(frozen=True)
class JwtOAuthDestination:
    """OAuth-protected endpoint. signing_key_pem is PEM-encoded RS256 key material."""

    token_url: str
    destination_url: str
    signing_key_pem: str
    jwt_issuer: str
    jwt_audience: str
    method: str = METHOD_POST
    jwt_algorithm: str = "RS256"
    jwt_key_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_url": self.token_url,
            "destination_url": self.destination_url,
            "signing_key_pem": self.signing_key_pem,
            "jwt_issuer": self.jwt_issuer,
            "jwt_audience": self.jwt_audience,
            "method": self.method,
            "jwt_algorithm": self.jwt_algorithm,
            "jwt_key_id": self.jwt_key_id,
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> JwtOAuthDestination:
        return JwtOAuthDestination(
            token_url=raw["token_url"],
            destination_url=raw["destination_url"],
            signing_key_pem=raw["signing_key_pem"],
            jwt_issuer=raw["jwt_issuer"],
            jwt_audience=raw["jwt_audience"],
            method=raw.get("method", METHOD_POST),
            jwt_algorithm=raw.get("jwt_algorithm", "RS256"),
            jwt_key_id=raw.get("jwt_key_id"),
        )


# Tagged union. Which variant a decrypted destination blob parses into is
# driven by the connector's destination_type field, not by anything inside
# the JSON itself.
Destination = WebhookDestination | KinesisDestination | JwtOAuthDestination

_DESTINATION_PARSERS: dict[str, Callable[[dict[str, Any]], Destination]] = {
    DESTINATION_WEBHOOK: WebhookDestination.from_dict,
    DESTINATION_KINESIS: KinesisDestination.from_dict,
    DESTINATION_JWT_OAUTH: JwtOAuthDestination.from_dict,
}


def parse_destination(destination_type: str, raw: dict[str, Any]) -> Destination:
    """Parse a decrypted destination JSON blob into the right variant."""
    try:
        parser = _DESTINATION_PARSERS[destination_type]
    except KeyError as exc:
        raise ValueError(f"unknown destination_type: {destination_type!r}") from exc
    return parser(raw)


def destination_to_dict(destination: Destination) -> dict[str, Any]:
    """Serialize any Destination variant back to a plain dict (for re-encryption)."""
    return destination.to_dict()


@dataclass
class Connector:
    """A row in the connectors table. Not frozen: connection_status and
    enabled are updated in place over the connector's lifetime."""

    tenant_id: str
    name: str
    payload_type: str
    destination_type: str
    destination: str  # encrypted at rest (base64 ciphertext); see crypto/kms.py
    transformation_name: str | None = None
    integration: IntegrationMetadata | None = None
    connection_status: ConnectionStatus | None = None
    enabled: bool = True

    @property
    def is_integration(self) -> bool:
        """Plain webhook connectors and integration connectors are
        distinguishable by the presence of the integration attribute."""
        return self.integration is not None

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "partition_key": self.tenant_id,
            "sort_key": self.name,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "payload_type": self.payload_type,
            "destination_type": self.destination_type,
            "destination": self.destination,
            "enabled": self.enabled,
        }
        if self.transformation_name is not None:
            item["transformation_name"] = self.transformation_name
        if self.integration is not None:
            item["integration"] = self.integration.to_item()
        if self.connection_status is not None:
            item["connection_status"] = self.connection_status.to_item()
        return item

    @staticmethod
    def from_item(item: dict[str, Any]) -> Connector:
        return Connector(
            tenant_id=item["tenant_id"],
            name=item["name"],
            payload_type=item["payload_type"],
            destination_type=item["destination_type"],
            destination=item["destination"],
            transformation_name=item.get("transformation_name"),
            integration=IntegrationMetadata.from_item(item.get("integration")),
            connection_status=ConnectionStatus.from_item(item.get("connection_status")),
            enabled=item.get("enabled", True),
        )


@dataclass(frozen=True)
class Transformation:
    """A row in the transformations table: a named JQ script for one tenant."""

    tenant_id: str
    name: str
    jq_script: str
    description: str | None = None

    def to_item(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "partition_key": self.tenant_id,
            "sort_key": self.name,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "jq_script": self.jq_script,
        }
        if self.description is not None:
            item["description"] = self.description
        return item

    @staticmethod
    def from_item(item: dict[str, Any]) -> Transformation:
        return Transformation(
            tenant_id=item["tenant_id"],
            name=item["name"],
            jq_script=item["jq_script"],
            description=item.get("description"),
        )


@dataclass(frozen=True)
class Progress:
    """One customer's offer progress entry within a BatchProgress message.

    `connectors` names exactly which connector rows the distributor must load
    and invoke for this event. `all_outcomes` is the list of state
    transitions that occurred (a batch can represent more than one transition
    if the distributor lagged); `status` is the current state.
    """

    tenant_id: str
    internal_customer_id: str
    offer_id: str
    campaign_id: str
    connectors: list[str]
    status: str
    campaign_window_start: str = ""
    campaign_window_end: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    all_outcomes: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Progress:
        return Progress(
            tenant_id=raw["tenant_id"],
            internal_customer_id=raw["internal_customer_id"],
            offer_id=raw["offer_id"],
            campaign_id=raw["campaign_id"],
            connectors=list(raw.get("connectors", [])),
            status=raw["status"],
            campaign_window_start=raw.get("campaign_window_start", ""),
            campaign_window_end=raw.get("campaign_window_end", ""),
            metadata=dict(raw.get("metadata", {})),
            all_outcomes=list(raw.get("all_outcomes", [])),
        )


@dataclass(frozen=True)
class BatchProgress:
    """One message off the plague Kinesis stream. Carries a trace block for
    distributed tracing, edge_time (UTC, when the platform emitted the
    event), and a progresses array covering one or more customers."""

    edge_time: str
    progresses: list[Progress]
    trace: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> BatchProgress:
        return BatchProgress(
            edge_time=raw.get("edge_time", ""),
            progresses=[Progress.from_dict(p) for p in raw.get("progresses", [])],
            trace=dict(raw.get("trace", {})),
        )


@dataclass(frozen=True)
class DeliveryResult:
    """Common return shape for webhook, kinesis, and oauth delivery, so the
    distributor writes connection_status the same way regardless of type."""

    success: bool
    status_code: int | None = None
    error: str | None = None
    delivered_at: str = field(default_factory=utc_now_iso)

    def to_connection_status(self) -> ConnectionStatus:
        return ConnectionStatus(
            status_code=self.status_code if self.status_code is not None else 0,
            timestamp=self.delivered_at,
        )
