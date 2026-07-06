"""Lambda entry point: audience-reducer.

Kinesis consumer on the audience-events stream. Each record is one customer's
set of audience add/remove changes (produced by P3's mparticle-processing when
it sees an audience_membership_change_request). For a batch of records:

  1. Decode and parse each record into an AudienceEvent
  2. Resolve every distinct external customer id to an internal UUID via MCI,
     batched across the whole Kinesis batch (one MCI round-trip, not one per
     record)
  3. Build one membership row per (customer, audience_change): add -> ELIGIBLE,
     delete -> INELIGIBLE (soft delete, never DeleteItem)
  4. Upsert all rows with 50x parallel DynamoDB writes

Trigger config (Terraform): batch_size 100, maximum_retry_attempts 7,
bisect_batch_on_function_error true, parallelization_factor up to 10. Parse
errors on a single record are logged and skipped so one malformed record does
not fail the batch; MCI or DynamoDB failures are raised so Lambda retries and
bisects to isolate the bad record.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import boto3
from mci.invoker.client import resolve_internal_ids
from mci.model.types import CustomerKey
from mci.parallel.pool import run_parallel

from audience_ingress.model.types import AudienceEvent, MembershipRow, utc_now_iso
from audience_ingress.store.membership import MembershipStore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MEMBERSHIP_TABLE_NAME = os.environ.get("MEMBERSHIP_TABLE_NAME", "")
MCI_FUNCTION_NAME = os.environ.get("MCI_FUNCTION_NAME", "")
MCI_FUNCTION_ALIAS = os.environ.get("MCI_FUNCTION_ALIAS", "LIVE")
WRITE_WORKERS = int(os.environ.get("DYNAMO_PARALLELIZATION_FACTOR", "50"))

_store: MembershipStore | None = None


def _get_store() -> MembershipStore:
    global _store
    if _store is None:
        table = boto3.resource("dynamodb").Table(MEMBERSHIP_TABLE_NAME)
        _store = MembershipStore(table)
    return _store


def _parse_records(records: list[dict]) -> list[AudienceEvent]:
    """Decode base64 Kinesis payloads into AudienceEvents, skipping bad ones."""
    events: list[AudienceEvent] = []
    for record in records:
        try:
            raw = base64.b64decode(record["kinesis"]["data"])
            events.append(AudienceEvent.from_record(json.loads(raw)))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("audience-reducer: skipping unparseable record: %s", exc)
    return events


def _resolve_batch(events: list[AudienceEvent]) -> dict[str, dict[str, str]]:
    """Resolve all distinct (tenant, external) pairs in the batch via MCI.

    Returns the nested {tenant: {external: internal}} map. Grouping by tenant
    keeps each MCI request tenant-consistent, matching how MCI keys its table.
    """
    keys_by_tenant: dict[str, set[str]] = {}
    for ev in events:
        keys_by_tenant.setdefault(ev.tenant_id, set()).add(ev.external_customer_id)

    resolved: dict[str, dict[str, str]] = {}
    for tenant_id, externals in keys_by_tenant.items():
        keys = [CustomerKey(tenant_id=tenant_id, customer_id=cid) for cid in externals]
        response = resolve_internal_ids(
            function_name=MCI_FUNCTION_NAME,
            customer_keys=keys,
            qualifier=MCI_FUNCTION_ALIAS,
        )
        resolved.update(response)
    return resolved


def _build_rows(
    events: list[AudienceEvent], resolved: dict[str, dict[str, str]]
) -> list[MembershipRow]:
    now = utc_now_iso()
    rows: list[MembershipRow] = []
    for ev in events:
        internal_id = resolved.get(ev.tenant_id, {}).get(ev.external_customer_id)
        if not internal_id:
            logger.error(
                "audience-reducer: no MCI mapping for tenant=%s external=%s, skipping",
                ev.tenant_id,
                ev.external_customer_id,
            )
            continue
        for change in ev.audience_changes:
            rows.append(
                MembershipRow(
                    tenant_id=ev.tenant_id,
                    audience_id=change.audience_id,
                    internal_customer_id=internal_id,
                    state=change.target_state,
                    updated_at=now,
                )
            )
    return rows


def handler(event: dict, context: object = None) -> None:
    """Kinesis trigger handler."""
    records = event.get("Records", [])
    events = _parse_records(records)
    if not events:
        logger.info("audience-reducer: no parseable records in batch")
        return

    resolved = _resolve_batch(events)
    rows = _build_rows(events, resolved)
    if not rows:
        logger.info("audience-reducer: batch produced no membership rows")
        return

    store = _get_store()
    # 50x parallel writes. run_parallel re-raises the first failure, which
    # propagates out of the handler and triggers Kinesis bisect-and-retry.
    run_parallel(rows, store.upsert, workers=WRITE_WORKERS)
    logger.info(
        "audience-reducer: upserted %d membership rows from %d events",
        len(rows),
        len(events),
    )
