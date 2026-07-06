"""Lambda entry point: audience-ingest.

First task in the batch Step Function. Given an uploaded gzipped CSV in S3:

  1. Download it from S3
  2. Decompress and parse, extracting unique external customer IDs
  3. Resolve those IDs to internal UUIDs via MCI, 5000 per invoke
  4. Return the internal IDs and counts for the next state (parquet writer)

The 5000 batch size matches the spec: MCI batches up to 5000 keys per call, so
one MCI invoke per 5000 external IDs keeps each call within its limit.

On new_mappings_created: the MCI invoker returns only the resolved
external->internal map, not which entries it had to create. Truthfully
populating new_mappings_created therefore needs a read-only probe pass first
(count what already exists, then subtract). That doubles MCI invokes, so it is
OFF by default (env MCI_PROBE_EXISTING). When off, new_mappings_created is
reported as 0 and should be read as "not measured", not "zero created". This is
called out rather than silently hardcoded.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from mci.invoker.client import resolve_internal_ids
from mci.model.types import CustomerKey

from audience_ingress.csv.reader import read_external_ids

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MCI_FUNCTION_NAME = os.environ.get("MCI_FUNCTION_NAME", "")
# Same alias deferral as P2/P3: LIVE is not deployed in dev, override to $LATEST.
MCI_FUNCTION_ALIAS = os.environ.get("MCI_FUNCTION_ALIAS", "LIVE")
MCI_BATCH_SIZE = int(os.environ.get("MCI_BATCH_SIZE", "5000"))
MCI_PROBE_EXISTING = os.environ.get("MCI_PROBE_EXISTING", "false").lower() == "true"

# Reused across warm invocations.
_s3_client: Any = None


def _get_s3() -> Any:
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _resolve_all(tenant_id: str, external_ids: list[str], *, read_only: bool) -> dict[str, str]:
    """Resolve every external id to internal, one MCI invoke per 5000.

    Returns a flat {external_id: internal_id} map for this tenant. Missing keys
    (an external id we sent but got nothing back for) are simply absent from the
    map; the caller counts them.
    """
    resolved: dict[str, str] = {}
    for chunk in _chunks(external_ids, MCI_BATCH_SIZE):
        keys = [CustomerKey(tenant_id=tenant_id, customer_id=cid) for cid in chunk]
        response = resolve_internal_ids(
            function_name=MCI_FUNCTION_NAME,
            customer_keys=keys,
            qualifier=MCI_FUNCTION_ALIAS,
            read_only=read_only,
        )
        resolved.update(response.get(tenant_id, {}))
    return resolved


def handler(event: dict, context: object = None) -> dict[str, Any]:
    """Step Function task handler.

    Input event: {s3_bucket, s3_key, tenant_id, audience_id}
    Output: {internal_customer_ids, count, external_ids_not_found, new_mappings_created}
    """
    s3_bucket = event["s3_bucket"]
    s3_key = event["s3_key"]
    tenant_id = event["tenant_id"]
    audience_id = event["audience_id"]

    logger.info(
        "audience-ingest: tenant=%s audience=%s s3://%s/%s",
        tenant_id,
        audience_id,
        s3_bucket,
        s3_key,
    )

    obj = _get_s3().get_object(Bucket=s3_bucket, Key=s3_key)
    gzipped = obj["Body"].read()
    external_ids = read_external_ids(gzipped)
    logger.info("audience-ingest: %d unique external ids parsed", len(external_ids))

    # Optional truthful new-mapping count: probe read-only first to see what
    # already exists, then create.
    new_mappings_created = 0
    if MCI_PROBE_EXISTING:
        pre_existing = _resolve_all(tenant_id, external_ids, read_only=True)
        new_mappings_created = len(external_ids) - len(pre_existing)

    resolved = _resolve_all(tenant_id, external_ids, read_only=False)

    internal_customer_ids = [resolved[cid] for cid in external_ids if cid in resolved]
    external_ids_not_found = len(external_ids) - len(internal_customer_ids)
    if external_ids_not_found:
        logger.warning(
            "audience-ingest: %d external ids had no MCI mapping returned",
            external_ids_not_found,
        )

    return {
        "tenant_id": tenant_id,
        "audience_id": audience_id,
        "internal_customer_ids": internal_customer_ids,
        "count": len(internal_customer_ids),
        "external_ids_not_found": external_ids_not_found,
        "new_mappings_created": new_mappings_created,
    }
