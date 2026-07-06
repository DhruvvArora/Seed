"""mParticle Firehose format to InternalEvent conversion.

Two responsibilities:
  1. Parse the raw mParticle Firehose JSON into typed objects (MParticleBatch).
  2. Convert those typed objects into canonical InternalEvent dicts.

Currency conversion
-------------------
mParticle sends monetary amounts as floats (dollars). The platform stores
everything as integer cents. The rule: multiply by 100 and round to the nearest
integer. Never store floats for money.

  47.50  -> round(47.50 * 100) = 4750  (exact)
  47.999 -> round(47.999 * 100) = 4800  (rounds up)
  47.001 -> round(47.001 * 100) = 4700  (rounds down)

We use Python's built-in round() which implements banker's rounding (round
half to even) for .5 cases, consistent with financial industry conventions.

Identity extraction
-------------------
mParticle user_identities is a list of {type, encoding, value} objects.
We look for the one where type == "customer":
  - encoding == "raw":  use the value directly
  - encoding == "md5":  use the value as-is (already hashed by mParticle)
  - no customer type:   caller should skip the batch

UTC timestamp handling
----------------------
mParticle's timestamp_ms is epoch milliseconds (true UTC). We use it for
event_utc_datetime and event_edge_datetime rather than the "assumed_local"
fallback, so utc_source is NOT set for mParticle events -- the UTC is real.
event_local_datetime is set to the same UTC value (mParticle does not send
local time separately).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from event_ingress.model.types import (
    Action,
    InternalEvent,
    MParticleBatch,
    MParticleEventItem,
    MParticleUserIdentity,
    Transaction,
    TransactionItem,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# mParticle event type strings we care about.
_MP_COMMERCE_EVENT = "commerce_event"
_MP_CUSTOM_EVENT = "custom_event"

# Attribute key written by mparticle-enqueue into the enriched batch.
_ATTR_SOURCE = "source"
_ATTR_SOURCE_VALUE = "mparticle"


def dollars_to_cents(amount_dollars: float) -> int:
    """Convert a float dollar amount to integer cents.

    Uses round() (banker's rounding) to handle floating-point imprecision.
    Example: 47.50 -> 4750, 0.99 -> 99.
    """
    return round(amount_dollars * 100)


def extract_customer_identity(
    user_identities: list[MParticleUserIdentity],
) -> str | None:
    """Return the external customer ID from user_identities, or None.

    Only "customer" type identities are used. Returns None if none is found,
    which signals the caller to skip and log the batch.
    """
    for identity in user_identities:
        if identity.identity_type == "customer":
            # Both "raw" and "md5" values are returned as-is; mParticle
            # has already applied the encoding if encoding == "md5".
            return identity.value
    return None


def parse_mparticle_batch(raw: dict) -> MParticleBatch:
    """Parse the raw Firehose JSON into a typed MParticleBatch.

    The raw dict here is the enriched payload written by mparticle-enqueue
    (which added tenant_id). It is NOT the original HTTP request body.
    """
    # User identities
    user_identities = [
        MParticleUserIdentity(
            identity_type=str(ui.get("identity_type", "")),
            encoding=str(ui.get("encoding", "raw")),
            value=str(ui.get("value", "")),
        )
        for ui in raw.get("user_identities", [])
    ]

    # Events
    events = [
        MParticleEventItem(
            event_type=str(e.get("event_type", "")),
            data=dict(e.get("data", {})),
        )
        for e in raw.get("events", [])
    ]

    return MParticleBatch(
        tenant_id=str(raw.get("tenant_id", "")),
        batch_id=str(raw.get("batch_id", "")),
        timestamp_ms=int(raw.get("timestamp_ms", 0)),
        message_type=str(raw.get("message_type", "")),
        user_identities=user_identities,
        user_attributes=dict(raw.get("user_attributes", {})),
        events=events,
        raw=raw,
    )


def _epoch_ms_to_iso(timestamp_ms: int) -> str:
    """Convert epoch milliseconds to an ISO 8601 UTC string."""
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _convert_commerce_event(
    mp_event: MParticleEventItem,
    batch: MParticleBatch,
    internal_customer_id: str,
    edge_datetime: str,
) -> InternalEvent | None:
    """Convert an mParticle commerce_event to a transaction InternalEvent.

    Returns None if the event does not look like a purchase (no total_amount).
    """
    data = mp_event.data
    total_amount_dollars: float | None = data.get("total_amount")
    if total_amount_dollars is None:
        logger.debug("commerce_event has no total_amount, skipping")
        return None

    total_cents = dollars_to_cents(float(total_amount_dollars))
    currency = str(data.get("currency_code", "USD"))

    # Build line items if present.
    items: dict[str, TransactionItem] = {}
    for i, product in enumerate(data.get("product_action", {}).get("products", [])):
        item_id = str(product.get("id", f"item_{i}"))
        price_cents = dollars_to_cents(float(product.get("price", 0.0)))
        items[item_id] = TransactionItem(
            category=str(product.get("category", "")),
            price=price_cents,
            qty=int(product.get("quantity", 1)),
        )

    transaction = Transaction(total=total_cents, currency=currency, items=items)

    # mParticle gives us real UTC (timestamp_ms is epoch ms).
    event_utc = _epoch_ms_to_iso(batch.timestamp_ms)
    event_id = str(data.get("event_id") or f"{batch.batch_id}_{id(mp_event)}")

    return InternalEvent(
        tenant_id=batch.tenant_id,
        customer_id=internal_customer_id,
        event_id=event_id,
        event_edge_datetime=edge_datetime,
        event_local_datetime=event_utc,  # mParticle does not send local time separately
        event_utc_datetime=event_utc,  # real UTC from timestamp_ms -- no assumed_local marker
        event_type="transaction",
        event_data=transaction.to_dict(),
        event_attributes={_ATTR_SOURCE: _ATTR_SOURCE_VALUE},
    )


def _convert_custom_event(
    mp_event: MParticleEventItem,
    batch: MParticleBatch,
    internal_customer_id: str,
    edge_datetime: str,
) -> InternalEvent:
    """Convert an mParticle custom_event to an action InternalEvent."""
    data = mp_event.data
    event_id = str(data.get("event_id") or f"{batch.batch_id}_{id(mp_event)}")
    action_id = str(data.get("event_name", "unknown"))
    attributes = {str(k): str(v) for k, v in data.get("custom_attributes", {}).items()}

    action = Action(action_id=action_id, attributes=attributes)
    event_utc = _epoch_ms_to_iso(batch.timestamp_ms)

    return InternalEvent(
        tenant_id=batch.tenant_id,
        customer_id=internal_customer_id,
        event_id=event_id,
        event_edge_datetime=edge_datetime,
        event_local_datetime=event_utc,
        event_utc_datetime=event_utc,
        event_type="action",
        event_data=action.to_dict(),
        event_attributes={_ATTR_SOURCE: _ATTR_SOURCE_VALUE},
    )


def convert_batch_to_internal_events(
    batch: MParticleBatch,
    internal_customer_id: str,
    edge_datetime: str,
) -> list[InternalEvent]:
    """Convert all events in a parsed mParticle batch to InternalEvent objects.

    Unrecognised event types are skipped with a debug log (not an error): the
    platform only processes events it understands; others are safely ignored.
    """
    results: list[InternalEvent] = []
    for mp_event in batch.events:
        event_type = mp_event.event_type
        if event_type == _MP_COMMERCE_EVENT:
            converted = _convert_commerce_event(
                mp_event, batch, internal_customer_id, edge_datetime
            )
            if converted is not None:
                results.append(converted)
        elif event_type == _MP_CUSTOM_EVENT:
            results.append(
                _convert_custom_event(mp_event, batch, internal_customer_id, edge_datetime)
            )
        else:
            logger.debug("mparticle-convert: skipping unknown event_type %r", event_type)
    return results
