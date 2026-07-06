"""Parquet serialisation for audience membership snapshots.

pyarrow/pandas are heavy and are NOT shipped in the Lambda zip; they come from
the AWS-managed AWSSDKPandas layer at runtime. So `awswrangler` is imported
lazily inside write_parquet, never at module load. That keeps this module
importable (and its unit tests collectable) in a plain dev/CI environment that
has no layer and no pyarrow.

The two pure helpers below (partition path, row building) carry the logic worth
testing and need no heavy deps, so they are covered by unit tests directly.
"""

from __future__ import annotations

from typing import Any

from audience_ingress.model.types import ParquetMemberRow


def s3_partition_path(
    bucket: str,
    tenant_id: str,
    run_date: str,
    audience_id: str,
    filename: str = "part-0001.parquet",
) -> str:
    """Build the Hive-style partitioned S3 key defined by the spec.

    s3://{bucket}/tenant_id={tenant}/run_date={YYYY-MM-DD}/audience_id={aud}/part-0001.parquet
    """
    return (
        f"s3://{bucket}/"
        f"tenant_id={tenant_id}/"
        f"run_date={run_date}/"
        f"audience_id={audience_id}/"
        f"{filename}"
    )


def build_rows(
    tenant_id: str,
    audience_id: str,
    run_date: str,
    internal_customer_ids: list[str],
    state: str,
) -> list[ParquetMemberRow]:
    """Expand a member list into typed parquet rows (all five schema columns)."""
    return [
        ParquetMemberRow(
            tenant_id=tenant_id,
            audience_id=audience_id,
            internal_customer_id=cid,
            run_date=run_date,
            state=state,
        )
        for cid in internal_customer_ids
    ]


def write_parquet(rows: list[ParquetMemberRow], s3_path: str, *, wr: Any = None) -> str:
    """Write `rows` as a single parquet file to `s3_path`; return the path.

    awswrangler (aliased wr) is imported lazily so this module loads without the
    layer. Tests inject a fake `wr` to avoid pyarrow entirely.
    """
    if wr is None:
        import awswrangler  # type: ignore[import-not-found]  # provided by the layer

        wr = awswrangler

    import pandas as pd  # type: ignore[import-not-found]  # provided by the layer

    frame = pd.DataFrame(
        [
            {
                "tenant_id": r.tenant_id,
                "audience_id": r.audience_id,
                "internal_customer_id": r.internal_customer_id,
                "run_date": r.run_date,
                "state": r.state,
            }
            for r in rows
        ]
    )
    # dataset=False -> one file written to exactly this key (no wrangler-managed
    # partition directories); the Hive layout is encoded in the path itself.
    wr.s3.to_parquet(df=frame, path=s3_path, dataset=False, index=False)
    return s3_path
