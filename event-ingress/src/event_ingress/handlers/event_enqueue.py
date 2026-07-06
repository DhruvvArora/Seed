"""Lambda entry point: event-enqueue.

Handles POST /event from API Gateway. This is the fast ingest path:
  1. Parse and validate the event body
  2. Extract tenant_id from the authorizer context (injected by joust)
  3. Call MCI to resolve external customer_id to internal UUID
  4. Build a canonical InternalEvent
  5. Put to the correct Kinesis stream by event_type
  6. Return {"status": "ok", "event_id": "<id>"}

Deduplication is NOT done here. The spec assigns deduplication to the
downstream consumer (scribe-writer / Execution Engine) using event_id. The
enqueue path is stateless and fast.

UTC timestamp handling
----------------------
The client sends event_time as a naive local datetime (no timezone offset).
We cannot derive a true UTC value without a per-tenant timezone. So:
  event_edge_datetime  = now() in UTC  (when we received the event)
  event_local_datetime = event_time as supplied
  event_utc_datetime   = event_local_datetime (best available)
  event_attributes["utc_source"] = "assumed_local"

Downstream consumers must check utc_source before treating event_utc_datetime
as authoritative UTC.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from mci.invoker.client import MciInvokeError, resolve_internal_ids
from mci.model.types import CustomerKey

from event_ingress.kinesis.producer import build_partition_key, put_event
from event_ingress.model.types import (
    UTC_SOURCE_ASSUMED,
    UTC_SOURCE_KEY,
    Action,
    InternalEvent,
    Transaction,
    TransactionItem,
)
from event_ingress.validate.event_validator import EventValidationError, validate_event

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-scope env (reads once per container).
TRANSACTIONS_STREAM_NAME = os.environ.get("TRANSACTIONS_STREAM_NAME", "")
ACTIONS_STREAM_NAME = os.environ.get("ACTIONS_STREAM_NAME", "")
MCI_FUNCTION_NAME = os.environ.get("MCI_FUNCTION_NAME", "")
MCI_FUNCTION_ALIAS = os.environ.get("MCI_FUNCTION_ALIAS", "LIVE")

# Lazily-built boto3 client so that importing this module in tests does not
# require real AWS credentials.
_kinesis_client: Any = None


def _get_kinesis():
    global _kinesis_client
    if _kinesis_client is None:
        import boto3

        _kinesis_client = boto3.client("kinesis")
    return _kinesis_client


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def _ok(event_id: str) -> dict:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "ok", "event_id": event_id}),
    }


def _error(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "error", "message": message}),
    }


# ---------------------------------------------------------------------------
# Event building
# ---------------------------------------------------------------------------


def _build_transaction(raw: dict) -> Transaction:
    tx = raw.get("transaction", {})
    items: dict[str, TransactionItem] = {}
    for item_id, item_data in tx.get("items", {}).items():
        items[item_id] = TransactionItem(
            category=str(item_data.get("category", "")),
            price=int(item_data.get("price", 0)),
            qty=int(item_data.get("qty", 1)),
        )
    return Transaction(
        total=int(tx["total"]),
        currency=str(tx.get("currency", "USD")),
        items=items,
    )


def _build_action(raw: dict) -> Action:
    action = raw.get("action", {})
    return Action(
        action_id=str(action.get("action_id", "")),
        attributes=dict(action.get("attributes", {})),
    )


def _build_internal_event(
    body: dict,
    tenant_id: str,
    internal_customer_id: str,
) -> InternalEvent:
    edge_dt = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    local_dt: str = body["event_time"]
    event_type: str = body["event_type"]

    if event_type == "transaction":
        event_data = _build_transaction(body).to_dict()
    else:
        event_data = _build_action(body).to_dict()

    return InternalEvent(
        tenant_id=tenant_id,
        customer_id=internal_customer_id,
        event_id=body["event_id"],
        event_edge_datetime=edge_dt,
        event_local_datetime=local_dt,
        # We cannot derive real UTC from naive local time. Record what we know
        # and mark the field so downstream consumers can identify these events.
        event_utc_datetime=local_dt,
        event_type=event_type,
        event_data=event_data,
        event_attributes={UTC_SOURCE_KEY: UTC_SOURCE_ASSUMED},
    )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(event: dict, context=None) -> dict:
    """API Gateway Lambda proxy handler for POST /event."""
    # Extract tenant_id injected by the joust authorizer.
    authorizer_context = event.get("requestContext", {}).get("authorizer", {})
    tenant_id: str = authorizer_context.get("tenant_id", "")
    if not tenant_id:
        logger.error("event-enqueue: tenant_id missing from authorizer context")
        return _error(401, "Unauthorized")

    # Parse body.
    raw_body = event.get("body") or "{}"
    try:
        body: dict = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        return _error(400, f"Invalid JSON: {exc}")

    # Validate.
    try:
        validate_event(body)
    except EventValidationError as exc:
        return _error(400, str(exc))

    external_customer_id: str = body["customer_id"]

    # Resolve external -> internal via MCI.
    try:
        resolved = resolve_internal_ids(
            function_name=MCI_FUNCTION_NAME,
            customer_keys=[CustomerKey(tenant_id=tenant_id, customer_id=external_customer_id)],
            qualifier=MCI_FUNCTION_ALIAS,
        )
    except MciInvokeError as exc:
        logger.error("event-enqueue: MCI invoke failed: %s", exc)
        return _error(500, "Internal error resolving customer identity")

    internal_customer_id = resolved.get(tenant_id, {}).get(external_customer_id)
    if not internal_customer_id:
        logger.error(
            "event-enqueue: MCI returned no mapping for tenant=%s external=%s",
            tenant_id,
            external_customer_id,
        )
        return _error(500, "Internal error resolving customer identity")

    # Build the canonical event.
    internal_event = _build_internal_event(body, tenant_id, internal_customer_id)

    # Route to the correct stream.
    stream = (
        TRANSACTIONS_STREAM_NAME
        if internal_event.event_type == "transaction"
        else ACTIONS_STREAM_NAME
    )
    partition_key = build_partition_key(tenant_id, internal_customer_id)

    try:
        put_event(
            stream_name=stream,
            event_dict=internal_event.to_dict(),
            partition_key=partition_key,
            kinesis_client=_get_kinesis(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("event-enqueue: Kinesis put failed: %s", exc)
        return _error(500, "Internal error writing to stream")

    logger.info(
        "event-enqueue: OK event_id=%s tenant=%s type=%s",
        internal_event.event_id,
        tenant_id,
        internal_event.event_type,
    )
    return _ok(internal_event.event_id)
