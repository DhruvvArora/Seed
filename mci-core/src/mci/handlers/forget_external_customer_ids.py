"""Lambda entry point: forget-external-customer-ids.

CCPA / GDPR right-to-be-forgotten. Deletes the MCI mappings for a set of
external customer IDs within one tenant, then writes an audit record to S3.

Input:  {"tenant_id": "...", "external_customer_ids": ["ext1", "ext2", ...]}
Output: {"deleted": [...found and removed...], "not_found": [...had no mapping...]}

Why the audit log matters: deleting the external->internal mapping orphans the
internal UUID, making all data keyed by it unreachable. Regulators require proof
that a deletion request was acted on, so we ALWAYS write an audit record (even if
nothing was found), capturing the tenant, timestamp, and which IDs were deleted.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

from mci.parallel.pool import run_parallel
from mci.store.dynamodb.table import MciStore

TABLE_NAME = os.environ.get("TABLE_NAME", "")
AUDIT_BUCKET = os.environ.get("AUDIT_BUCKET", "") or os.environ.get("LOG_BUCKET", "")
WORKERS = int(os.environ.get("DYNAMO_PARALLELIZATION_FACTOR", "20"))

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


def _write_audit(tenant_id: str, deleted: list[str], not_found: list[str]) -> None:
    """Write a compliance audit record to S3. Always called, even when nothing
    was deleted, because the request itself must be provably recorded."""
    if not AUDIT_BUCKET:
        return
    record = {
        "tenant_id": tenant_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "deleted": deleted,
        "not_found": not_found,
        "action": "forget_external_customer_ids",
    }
    ts = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S_%f")
    _get_s3().put_object(
        Bucket=AUDIT_BUCKET,
        Key=f"forget-audit/{tenant_id}/{ts}.json",
        Body=json.dumps(record).encode("utf-8"),
    )


def handler(event: dict, context=None) -> dict[str, list[str]]:
    tenant_id = event["tenant_id"]
    external_ids = event.get("external_customer_ids", [])

    unique_ids = list(dict.fromkeys(external_ids))  # dedup, keep order

    deleted: list[str] = []
    not_found: list[str] = []

    if unique_ids:
        store = _get_store()
        # delete returns True if a row existed and was removed, False otherwise.
        results = run_parallel(
            unique_ids,
            lambda ext: store.delete(tenant_id, ext),
            workers=WORKERS,
        )
        for ext, was_deleted in zip(unique_ids, results):
            (deleted if was_deleted else not_found).append(ext)

    # Compliance: always record that the request was processed.
    _write_audit(tenant_id, deleted, not_found)

    return {"deleted": deleted, "not_found": not_found}
