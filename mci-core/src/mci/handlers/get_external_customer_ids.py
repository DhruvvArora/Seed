"""Lambda entry point: get-external-customer-ids.

Reverse lookup. Given a list of internal UUIDs for one tenant, return the
external IDs they map to. Used for debugging and GDPR data export.

Input:  {"tenant_id": "...", "internal_customer_ids": ["uuid1", "uuid2", ...]}
Output: {"uuid1": "external1", "uuid2": "external2", ...}  (flat map)

Lookups go through the internal-customer-id GSI (see store.query_by_internal_id)
because the main table cannot be searched by internal UUID. UUIDs with no
mapping are simply omitted from the output.
"""

from __future__ import annotations

import os

from mci.parallel.pool import run_parallel
from mci.store.dynamodb.table import MciStore

TABLE_NAME = os.environ.get("TABLE_NAME", "")
WORKERS = int(os.environ.get("DYNAMO_PARALLELIZATION_FACTOR", "20"))

_store: MciStore | None = None


def _get_store() -> MciStore:
    global _store
    if _store is None:
        _store = MciStore(TABLE_NAME)
    return _store


def handler(event: dict, context=None) -> dict[str, str]:
    tenant_id = event["tenant_id"]
    internal_ids = event.get("internal_customer_ids", [])

    # Deduplicate while preserving order.
    unique_ids = list(dict.fromkeys(internal_ids))
    if not unique_ids:
        return {}

    store = _get_store()

    # One GSI query per UUID, run in parallel. Each returns the external id or
    # None. We pair results back with their UUIDs by position (run_parallel
    # preserves order).
    externals = run_parallel(
        unique_ids,
        lambda uid: store.query_by_internal_id(tenant_id, uid),
        workers=WORKERS,
    )

    output: dict[str, str] = {}
    for uid, external in zip(unique_ids, externals, strict=True):
        if external is not None:  # omit UUIDs with no mapping
            output[uid] = external
    return output
