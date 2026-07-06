"""Tests for the audience metadata store helpers."""

from __future__ import annotations

import pytest

from audience_ingress.model.types import (
    METADATA_SORT_KEY,
    SIZE_NOT_LOADED,
    STATE_ELIGIBLE,
    STATE_INELIGIBLE,
)
from audience_ingress.store.metadata import completion_fields, initial_metadata_item


def test_initial_row_uses_not_loaded_sentinel() -> None:
    item = initial_metadata_item("tenant-abc", "aud-a")
    assert item["partition_key"] == "tenant-abc#aud-a"
    assert item["sort_key"] == METADATA_SORT_KEY
    assert item["size"] == SIZE_NOT_LOADED == -1
    assert item["state"] == STATE_INELIGIBLE


def test_completion_fields_set_real_size_and_eligible() -> None:
    fields = completion_fields(size=1234, parquet_path="s3://b/k", run_date="2026-07-05")
    assert fields["size"] == 1234
    assert fields["state"] == STATE_ELIGIBLE
    assert fields["parquet_path"] == "s3://b/k"
    assert fields["run_date"] == "2026-07-05"
    assert "last_upload_time" in fields


def test_completion_rejects_sentinel_size() -> None:
    # Guards against ever writing the -1 sentinel (or any negative) as a
    # completed size, which would defeat the not-ready signal.
    with pytest.raises(ValueError, match="real member count"):
        completion_fields(size=-1, parquet_path="s3://b/k", run_date="2026-07-05")
