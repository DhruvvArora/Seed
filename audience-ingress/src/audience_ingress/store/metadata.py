"""Audience metadata store: bullseye-audience-metadata-v2.

Ownership split in the batch pipeline:

  * Initial row (size = -1): created here / by seeding. -1 is the "not loaded
    yet" sentinel that downstream offer-build keys off. An audience that exists
    but has no membership must read as -1, never as 0 or a partial count.

  * Completion update (state = ELIGIBLE, size = real count): written by the
    Step Function's final UpdateMetadata state as a single direct DynamoDB
    updateItem, NOT by a Lambda. That single write is what makes the size flip
    atomic -- there is never a window where size is a partial count.

This module therefore does not perform the completion write itself. It owns the
initial row and it owns `completion_fields()`, the canonical set of attributes
the ASL update must set. Keeping that list in one tested place means the ASL
and any future programmatic writer cannot silently drift apart.
"""

from __future__ import annotations

from typing import Any

from audience_ingress.model.types import (
    METADATA_SORT_KEY,
    SIZE_NOT_LOADED,
    STATE_ELIGIBLE,
    STATE_INELIGIBLE,
    build_partition_key,
    utc_now_iso,
)


def initial_metadata_item(tenant_id: str, audience_id: str) -> dict[str, Any]:
    """A freshly-created audience row: exists, but no membership loaded.

    state is INELIGIBLE and size is -1 until a load completes. Writing this is
    idempotent when guarded by attribute_not_exists on the partition key so a
    re-created audience does not clobber a real size.
    """
    return {
        "partition_key": build_partition_key(tenant_id, audience_id),
        "sort_key": METADATA_SORT_KEY,
        "tenant_id": tenant_id,
        "audience_id": audience_id,
        "state": STATE_INELIGIBLE,
        "size": SIZE_NOT_LOADED,
    }


def completion_fields(size: int, parquet_path: str, run_date: str) -> dict[str, Any]:
    """The canonical attribute set the completion update writes.

    This is the single source of truth for what "load complete" means on the
    metadata row. The Step Function's UpdateMetadata state sets exactly these,
    plus last_upload_time, in one updateItem. size is the real member count;
    passing a partial count here would defeat the -1 sentinel, so callers must
    pass the final count only.
    """
    if size < 0:
        raise ValueError("completion size must be the real member count, not the -1 sentinel")
    return {
        "state": STATE_ELIGIBLE,
        "size": size,
        "parquet_path": parquet_path,
        "run_date": run_date,
        "last_upload_time": utc_now_iso(),
    }
