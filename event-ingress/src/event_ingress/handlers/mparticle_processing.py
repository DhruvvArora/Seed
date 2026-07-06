"""Lambda entry point: mparticle-processing.

Kinesis consumer triggered by the mparticle-enqueue stream. For each record:
  1. Deserialise the enriched mParticle batch written by mparticle-enqueue
  2. Identify the message type:
       - "event_processing_request": extract customer identity, call MCI,
         convert events to InternalEvent, route to transactions-internal or
         action-internal
       - "audience_membership_change_request": log-and-skip (Project 4 will
         wire the real audience-events put when it builds the audience-reducer)
       - anything else: log-and-skip
  3. Batches where no "customer" type identity is found are skipped and logged
     as a deliberate no-op, not an error (spec requirement)

Kinesis trigger config (set in Terraform):
  batch_size: 100
  maximum_retry_attempts: 6
  bisect_batch_on_function_error: true

The bisect-on-error setting means if this handler raises, Lambda splits the
batch and retries each half -- eventually isolating the bad record. Individual
record errors should be caught here and logged rather than surfaced as
exceptions unless the error is truly unrecoverable for the whole batch.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from mci.invoker.client import MciInvokeError, resolve_internal_ids
from mci.model.types import CustomerKey

from event_ingress.convert.mparticle import (
    convert_batch_to_internal_events,
    extract_customer_identity,
    parse_mparticle_batch,
)
from event_ingress.kinesis.producer import build_partition_key, put_records_batch
from event_ingress.model.types import InternalEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TRANSACTIONS_STREAM_NAME = os.environ.get("TRANSACTIONS_STREAM_NAME", "")
ACTIONS_STREAM_NAME = os.environ.get("ACTIONS_STREAM_NAME", "")
MCI_FUNCTION_NAME = os.environ.get("MCI_FUNCTION_NAME", "")
MCI_FUNCTION_ALIAS = os.environ.get("MCI_FUNCTION_ALIAS", "LIVE")

_kinesis_client: Any = None


def _get_kinesis():
    global _kinesis_client
    if _kinesis_client is None:
        import boto3

        _kinesis_client = boto3.client("kinesis")
    return _kinesis_client


def _route_events(events: list[InternalEvent]) -> None:
    """Route a list of InternalEvents to the correct Kinesis stream."""
    tx_records: list[dict] = []
    action_records: list[dict] = []

    for ie in events:
        pk = build_partition_key(ie.tenant_id, ie.customer_id)
        record = {"Data": json.dumps(ie.to_dict()).encode("utf-8"), "PartitionKey": pk}
        if ie.event_type == "transaction":
            tx_records.append(record)
        else:
            action_records.append(record)

    kinesis = _get_kinesis()
    if tx_records:
        put_records_batch(TRANSACTIONS_STREAM_NAME, tx_records, kinesis_client=kinesis)
    if action_records:
        put_records_batch(ACTIONS_STREAM_NAME, action_records, kinesis_client=kinesis)


def _process_record(raw_data: dict) -> None:
    """Process one enriched mParticle batch."""
    message_type = raw_data.get("message_type", "")

    # Audience membership changes are handled by Project 4. Log and skip so
    # mparticle-processing does not error on valid mParticle traffic that
    # arrives before the audience-reducer is deployed.
    if message_type == "audience_membership_change_request":
        logger.info(
            "mparticle-processing: audience_membership_change_request -- "
            "skipping (handled by Project 4 audience-reducer)"
        )
        return

    if message_type != "event_processing_request":
        logger.warning("mparticle-processing: unrecognised message_type %r, skipping", message_type)
        return

    batch = parse_mparticle_batch(raw_data)

    # Identity extraction. No "customer" type identity means we cannot resolve
    # the customer -- skip the batch rather than error.
    external_customer_id = extract_customer_identity(batch.user_identities)
    if external_customer_id is None:
        logger.info(
            "mparticle-processing: batch_id=%s has no customer identity, skipping",
            batch.batch_id,
        )
        return

    # Resolve external -> internal via MCI.
    try:
        resolved = resolve_internal_ids(
            function_name=MCI_FUNCTION_NAME,
            customer_keys=[
                CustomerKey(tenant_id=batch.tenant_id, customer_id=external_customer_id)
            ],
            qualifier=MCI_FUNCTION_ALIAS,
        )
    except MciInvokeError as exc:
        logger.error(
            "mparticle-processing: MCI invoke failed for batch_id=%s: %s",
            batch.batch_id,
            exc,
        )
        raise  # Let Kinesis retry via bisect-on-error.

    internal_customer_id = resolved.get(batch.tenant_id, {}).get(external_customer_id)
    if not internal_customer_id:
        logger.error(
            "mparticle-processing: no MCI mapping returned for tenant=%s external=%s",
            batch.tenant_id,
            external_customer_id,
        )
        return

    edge_datetime = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    internal_events = convert_batch_to_internal_events(batch, internal_customer_id, edge_datetime)

    if not internal_events:
        logger.debug(
            "mparticle-processing: batch_id=%s produced no convertible events", batch.batch_id
        )
        return

    _route_events(internal_events)
    logger.info(
        "mparticle-processing: batch_id=%s -> %d events routed for tenant=%s",
        batch.batch_id,
        len(internal_events),
        batch.tenant_id,
    )


def handler(event: dict, context=None) -> None:
    """Kinesis trigger handler. Processes each record independently."""
    records = event.get("Records", [])
    for record in records:
        # Kinesis data arrives as base64-encoded bytes.
        try:
            raw_bytes = base64.b64decode(record["kinesis"]["data"])
            raw_data: dict = json.loads(raw_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.error("mparticle-processing: failed to deserialise Kinesis record: %s", exc)
            continue  # Skip undeserializable records; don't poison the whole batch.

        try:
            _process_record(raw_data)
        except MciInvokeError:
            raise  # Re-raise MCI failures so Kinesis bisects and retries.
        except Exception as exc:  # noqa: BLE001
            logger.error("mparticle-processing: unhandled error processing record: %s", exc)
            # Swallow per-record errors so the rest of the batch is processed.
            # CloudWatch alarms on Lambda errors will surface systemic failures.
