"""Typed data structures for the Event Ingress Service.

Two distinct domains live here:

  1. The canonical InternalEvent format: what ends up on the internal Kinesis
     streams (transactions-internal, action-internal). Every event, regardless
     of whether it arrived via the REST API or the mParticle Firehose, must be
     converted to this shape before going to Kinesis.

  2. The mParticle inbound types: the raw Firehose payload from mParticle, used
     only in mparticle-enqueue and mparticle-processing. Not exposed to callers
     outside those two handlers.

UTC timestamp note
------------------
The platform receives event_time as a naive local datetime (no timezone offset)
from both REST API clients and mParticle. We cannot derive a true UTC value from
a naive local time without a per-tenant timezone -- something we do not yet have.

The contract:
  event_local_datetime  -- stored as received, no modification
  event_edge_datetime   -- UTC timestamp when this Lambda processed the event
  event_utc_datetime    -- set equal to event_local_datetime (best available)
  event_attributes["utc_source"] -- always "assumed_local" when set this way

Downstream consumers must check utc_source before treating event_utc_datetime
as a true UTC value. When per-tenant timezone data is available, a correction
pass can reprocess events where utc_source == "assumed_local" and replace the
field with the real UTC value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Sentinel value written into event_attributes to mark events where
# event_utc_datetime was derived from a naive local time, not a real offset.
UTC_SOURCE_ASSUMED = "assumed_local"
UTC_SOURCE_KEY = "utc_source"


@dataclass
class TransactionItem:
    """One line item in a transaction."""

    category: str
    price: int  # integer cents
    qty: int


@dataclass
class Transaction:
    """Purchase event body. All monetary values in integer cents."""

    total: int  # integer cents
    currency: str
    items: dict[str, TransactionItem]  # keyed by line-item ID

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "currency": self.currency,
            "items": {
                k: {"category": v.category, "price": v.price, "qty": v.qty}
                for k, v in self.items.items()
            },
        }


@dataclass
class Action:
    """Non-purchase customer action body."""

    action_id: str
    attributes: dict[str, str]

    def to_dict(self) -> dict:
        return {"action_id": self.action_id, "attributes": self.attributes}


@dataclass
class InternalEvent:
    """Canonical event format written to transactions-internal or action-internal.

    Both inbound paths (REST API and mParticle Firehose) convert their payloads
    to this shape before calling kinesis.producer.put_event.
    """

    tenant_id: str
    customer_id: str  # INTERNAL UUID from MCI -- never the external ID
    event_id: str  # globally unique; used for downstream dedup
    event_edge_datetime: str  # UTC ISO8601 -- when this Lambda received it
    event_local_datetime: str  # naive local ISO8601 -- as supplied by the client
    event_utc_datetime: str  # ISO8601 -- see UTC note in module docstring
    event_type: str  # "transaction" or "action"
    event_data: dict  # serialized Transaction or Action
    event_attributes: dict = field(default_factory=dict)  # source metadata

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "customer_id": self.customer_id,
            "event_id": self.event_id,
            "event_edge_datetime": self.event_edge_datetime,
            "event_local_datetime": self.event_local_datetime,
            "event_utc_datetime": self.event_utc_datetime,
            "event_type": self.event_type,
            "event_data": self.event_data,
            "event_attributes": self.event_attributes,
        }


# ---------------------------------------------------------------------------
# mParticle inbound types
# ---------------------------------------------------------------------------


@dataclass
class MParticleUserIdentity:
    """One entry in the user_identities array from the Firehose payload."""

    identity_type: str  # e.g. "customer", "email"
    encoding: str  # "raw" or "md5"
    value: str


@dataclass
class MParticleEventItem:
    """One event within an mParticle batch."""

    event_type: str  # e.g. "commerce_event", "custom_event"
    data: dict  # raw event data -- parsed by the converter


@dataclass
class MParticleBatch:
    """The enriched mParticle batch written to the mparticle-enqueue Kinesis stream.

    This is NOT the raw Firehose payload. mparticle-enqueue adds tenant_id and
    writes this to Kinesis; mparticle-processing reads it and converts to
    InternalEvent.
    """

    tenant_id: str
    batch_id: str
    timestamp_ms: int
    message_type: str  # "event_processing_request" or "audience_membership_change_request"
    user_identities: list[MParticleUserIdentity]
    user_attributes: dict
    events: list[MParticleEventItem]
    raw: dict  # the original Firehose payload, preserved for debugging

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "batch_id": self.batch_id,
            "timestamp_ms": self.timestamp_ms,
            "message_type": self.message_type,
            "user_identities": [
                {"identity_type": i.identity_type, "encoding": i.encoding, "value": i.value}
                for i in self.user_identities
            ],
            "user_attributes": self.user_attributes,
            "events": [{"event_type": e.event_type, "data": e.data} for e in self.events],
            "raw": self.raw,
        }
