"""Lambda entry point: get-internal-customer-ids.

Resolves a batch of (tenant_id, external_customer_id) pairs to stable internal
UUIDs, creating mappings on miss. This is the orchestration layer: it does no
DynamoDB work directly, it coordinates the model, store, and pool pieces.

Flow (numbers map to the spec's implementation requirements):
  1. Parse + deduplicate input keys
  2. One batch_get to find keys that already exist
  3/4. For missing keys, put_if_absent (conditional create + race re-read)
  5. Run those creates in parallel (20 workers)
  6. Log newly created mappings to S3 as JSON lines
  7. Fail fast: any error aborts the whole batch (no partial results)

Module-scope setup (clients, store) runs ONCE per warm Lambda container and is
reused across invocations. Creating these inside handler() would pay the cost
on every call.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

from mci.model.types import CustomerKey, build_partition_key
from mci.parallel.pool import run_parallel
from mci.store.dynamodb.table import MciStore

# ----- module-scope init (runs once per container) ---------------------------

TABLE_NAME = os.environ.get("TABLE_NAME", "")
LOG_BUCKET = os.environ.get("LOG_BUCKET", "")
WORKERS = int(os.environ.get("DYNAMO_PARALLELIZATION_FACTOR", "20"))
READ_ONLY_ENV = os.environ.get("READ_ONLY", "false").lower() == "true"

# Lazily-built singletons so importing this module (e.g. in tests) does not
# require AWS credentials. They are created on first use inside the handler.
_store: MciStore | None = None
_s3_client = None


def _get_store() -> MciStore:
    global _store
    if _store is None:
        _store = MciStore(TABLE_NAME)
    return _store


def _get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


# ----- helpers ---------------------------------------------------------------


def _parse_keys(event: dict) -> list[CustomerKey]:
    """Turn the raw event into CustomerKey objects, deduplicated (requirement 1).

    Dedup uses an ordered dict keyed on the partition key so we preserve first-
    seen order while dropping repeats.
    """
    raw_keys = event.get("customer_keys", [])
    seen: dict[str, CustomerKey] = {}
    for entry in raw_keys:
        tenant_id = entry["tenant_id"]
        customer_id = entry["customer_id"]
        pk = build_partition_key(tenant_id, customer_id)
        if pk not in seen:
            seen[pk] = CustomerKey(tenant_id=tenant_id, customer_id=customer_id)
    return list(seen.values())


def _log_new_mappings(new_mappings: list[dict]) -> None:
    """Write newly created mappings to S3 as JSON lines (requirement 6).

    One object per invocation, keyed by timestamp. Skipped if no new mappings
    were created or no LOG_BUCKET is configured.
    """
    if not new_mappings or not LOG_BUCKET:
        return
    body = "\n".join(json.dumps(m) for m in new_mappings)
    ts = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S_%f")
    _get_s3().put_object(
        Bucket=LOG_BUCKET,
        Key=f"new-mappings/{ts}.jsonl",
        Body=body.encode("utf-8"),
    )


# ----- handler ---------------------------------------------------------------


def handler(event: dict, context=None) -> dict:
    """Resolve a batch of external customer IDs to internal UUIDs.

    Returns the nested map: {tenant_id: {external_customer_id: internal_uuid}}.
    """
    read_only = bool(event.get("read_only", READ_ONLY_ENV))
    keys = _parse_keys(event)

    store = _get_store()

    # Step 2: one bulk read to find who already exists.
    existing = store.batch_get(keys)  # partition_key -> MciItem

    # Split into already-resolved vs. needs-creating.
    resolved: dict[str, str] = {}  # partition_key -> internal uuid
    missing: list[CustomerKey] = []
    for key in keys:
        pk = build_partition_key(key.tenant_id, key.customer_id)
        item = existing.get(pk)
        if item is not None:
            resolved[pk] = item.internal_customer_id
        else:
            missing.append(key)

    # Steps 3/4/5: create the missing ones in parallel. put_if_absent handles
    # the conditional write and the race re-read; run_parallel handles the 20x
    # concurrency and fails fast if any single create errors (requirement 7).
    new_mappings: list[dict] = []
    if missing:
        created_uuids = run_parallel(
            missing,
            lambda k: store.put_if_absent(k, read_only=read_only),
            workers=WORKERS,
        )
        for key, internal_uuid in zip(missing, created_uuids):
            pk = build_partition_key(key.tenant_id, key.customer_id)
            resolved[pk] = internal_uuid
            new_mappings.append(
                {
                    "tenant_id": key.tenant_id,
                    "external_customer_id": key.customer_id,
                    "internal_customer_id": internal_uuid,
                }
            )

    # Step 6: persist the audit log of new mappings.
    _log_new_mappings(new_mappings)

    # Build the nested output map: tenant -> external -> internal.
    output: dict[str, dict[str, str]] = {}
    for key in keys:
        pk = build_partition_key(key.tenant_id, key.customer_id)
        output.setdefault(key.tenant_id, {})[key.customer_id] = resolved[pk]

    return output
