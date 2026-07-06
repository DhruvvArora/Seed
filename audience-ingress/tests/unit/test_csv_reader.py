"""Tests for the gzipped CSV reader."""

from __future__ import annotations

import gzip

import pytest

from audience_ingress.csv.reader import read_external_ids


def _gzip_csv(rows: list[tuple[str, str]]) -> bytes:
    lines = ["customer_id,audience_id"]
    lines += [f"{cid},{aud}" for cid, aud in rows]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def test_csv_parser_valid_file() -> None:
    data = _gzip_csv([("cust-1", "aud-a"), ("cust-2", "aud-a"), ("cust-3", "aud-a")])
    assert read_external_ids(data) == ["cust-1", "cust-2", "cust-3"]


def test_csv_parser_duplicate_ids() -> None:
    # cust-1 appears twice; output keeps one, first-seen order preserved.
    data = _gzip_csv(
        [("cust-1", "aud-a"), ("cust-2", "aud-a"), ("cust-1", "aud-a"), ("cust-3", "aud-a")]
    )
    assert read_external_ids(data) == ["cust-1", "cust-2", "cust-3"]


def test_csv_parser_skips_blank_ids() -> None:
    data = _gzip_csv([("cust-1", "aud-a"), ("", "aud-a"), ("cust-2", "aud-a")])
    assert read_external_ids(data) == ["cust-1", "cust-2"]


def test_csv_parser_missing_column_raises() -> None:
    bad = gzip.compress(b"wrong_col,audience_id\nx,aud-a\n")
    with pytest.raises(ValueError, match="customer_id"):
        read_external_ids(bad)
