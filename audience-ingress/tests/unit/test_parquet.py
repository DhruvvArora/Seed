"""Tests for the parquet pure helpers (no pyarrow needed)."""

from __future__ import annotations

from audience_ingress.model.types import STATE_ELIGIBLE
from audience_ingress.parquet.writer import build_rows, s3_partition_path, write_parquet


def test_s3_partition_path_is_hive_style() -> None:
    path = s3_partition_path("dev-audience-membership", "tenant-abc", "2026-07-05", "aud-hs-q1")
    assert path == (
        "s3://dev-audience-membership/"
        "tenant_id=tenant-abc/"
        "run_date=2026-07-05/"
        "audience_id=aud-hs-q1/"
        "part-0001.parquet"
    )


def test_build_rows_maps_every_id_with_state() -> None:
    rows = build_rows("tenant-abc", "aud-a", "2026-07-05", ["u1", "u2"], STATE_ELIGIBLE)
    assert [r.internal_customer_id for r in rows] == ["u1", "u2"]
    assert all(r.state == STATE_ELIGIBLE for r in rows)
    assert all(r.run_date == "2026-07-05" for r in rows)


def test_write_parquet_uses_injected_wrangler() -> None:
    # pandas builds the DataFrame; it is not a CI dep, so skip when absent.
    # The pure helpers above already cover path + row logic without it.
    import pytest

    pytest.importorskip("pandas")

    # Fake awswrangler: capture the df + path without needing pyarrow.
    captured: dict = {}

    class _FakeS3:
        def to_parquet(self, df, path, dataset, index):  # noqa: ANN001
            captured["path"] = path
            captured["rows"] = len(df)
            captured["cols"] = list(df.columns)
            captured["dataset"] = dataset

    class _FakeWr:
        s3 = _FakeS3()

    rows = build_rows("tenant-abc", "aud-a", "2026-07-05", ["u1", "u2", "u3"], STATE_ELIGIBLE)
    out = write_parquet(rows, "s3://bucket/key/part-0001.parquet", wr=_FakeWr())

    assert out == "s3://bucket/key/part-0001.parquet"
    assert captured["rows"] == 3
    assert captured["dataset"] is False
    assert captured["cols"] == [
        "tenant_id",
        "audience_id",
        "internal_customer_id",
        "run_date",
        "state",
    ]
