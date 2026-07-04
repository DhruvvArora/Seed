"""Typed request/response/item structures for MCI.

Mirrors the Go `internal/model` package. These dataclasses give us a single
source of truth for the shapes the spec describes, so handlers and the store
layer never pass raw dicts around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

SORT_KEY_PLACEHOLDER = "NULL"  # spec: sort_key is always the literal string "NULL"


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp for audit events, e.g. '2026-06-20T18:30:00+00:00'."""
    return datetime.now(UTC).isoformat()


def build_partition_key(tenant_id: str, external_customer_id: str) -> str:
    """The DynamoDB hash key format defined by the spec: {tenant_id}#{external_id}."""
    return f"{tenant_id}#{external_customer_id}"


@dataclass(frozen=True)
class CustomerKey:
    """One input pair to resolve. `customer_id` is the EXTERNAL id from a source system."""

    tenant_id: str
    customer_id: str


@dataclass
class AuditEvent:
    """One entry in an item's `events` audit list."""

    action: str  # e.g. "create_item", "add_internal_customer_id"
    time: str = field(default_factory=utc_now_iso)

    def to_item(self) -> dict[str, str]:
        return {"action": self.action, "time": self.time}


@dataclass
class MciItem:
    """A single row in the master-customer-index table."""

    tenant_id: str
    external_customer_id: str
    internal_customer_id: str
    events: list[AuditEvent] = field(default_factory=list)

    @property
    def partition_key(self) -> str:
        return build_partition_key(self.tenant_id, self.external_customer_id)

    def to_item(self) -> dict[str, object]:
        """Serialize to the DynamoDB attribute map (plain Python types; boto3
        resource layer handles the type descriptors)."""
        return {
            "partition_key": self.partition_key,
            "sort_key": SORT_KEY_PLACEHOLDER,
            "tenant_id": self.tenant_id,
            "external_customer_id": self.external_customer_id,
            "internal_customer_id": self.internal_customer_id,
            "events": [e.to_item() for e in self.events],
        }

    @classmethod
    def from_item(cls, item: dict[str, object]) -> MciItem:
        """Reconstruct from a DynamoDB attribute map."""
        raw_events = cast("list[dict[str, str]]", item.get("events") or [])
        events = [AuditEvent(action=str(e["action"]), time=str(e["time"])) for e in raw_events]
        return cls(
            tenant_id=str(item["tenant_id"]),
            external_customer_id=str(item["external_customer_id"]),
            internal_customer_id=str(item["internal_customer_id"]),
            events=events,
        )
