"""Lambda entry point: audience-parquet-writer.

Second task in the batch Step Function. Given the internal IDs resolved by
audience-ingest, write a single parquet file to the Hive-partitioned membership
bucket and return its S3 path for the metadata update.

Runs with the AWSSDKPandas layer attached (pyarrow/pandas). This handler
imports the parquet writer module, which imports awswrangler lazily, so the
module still loads in environments without the layer (e.g. CI).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from audience_ingress.model.types import STATE_ELIGIBLE
from audience_ingress.parquet.writer import build_rows, s3_partition_path, write_parquet

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MEMBERSHIP_BUCKET = os.environ.get("AUDIENCE_MEMBERSHIP_BUCKET", "")


def handler(event: dict, context: object = None) -> dict[str, Any]:
    """Step Function task handler.

    Input event: {tenant_id, audience_id, run_date?, internal_customer_ids}
    Output: {tenant_id, audience_id, run_date, parquet_path, count}
    """
    tenant_id = event["tenant_id"]
    audience_id = event["audience_id"]
    internal_customer_ids = event["internal_customer_ids"]
    # run_date defaults to today (UTC) when the caller does not pin one.
    run_date = event.get("run_date") or datetime.now(UTC).strftime("%Y-%m-%d")

    rows = build_rows(
        tenant_id=tenant_id,
        audience_id=audience_id,
        run_date=run_date,
        internal_customer_ids=internal_customer_ids,
        state=STATE_ELIGIBLE,
    )
    s3_path = s3_partition_path(MEMBERSHIP_BUCKET, tenant_id, run_date, audience_id)
    parquet_path = write_parquet(rows, s3_path)

    logger.info("audience-parquet-writer: wrote %d rows to %s", len(rows), parquet_path)

    return {
        "tenant_id": tenant_id,
        "audience_id": audience_id,
        "run_date": run_date,
        "parquet_path": parquet_path,
        "count": len(rows),
    }
