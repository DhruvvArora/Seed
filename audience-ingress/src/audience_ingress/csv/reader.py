"""Gzipped CSV reader for audience batch uploads.

Input files are gzipped CSVs with two columns, customer_id and audience_id,
one row per audience member (see the spec's file format). This module handles
just the parsing and deduplication; downloading from S3 and MCI resolution
live in the handler.

Kept as pure functions over bytes/streams so tests can feed in-memory gzip
data with no S3 or filesystem involved.
"""

from __future__ import annotations

import csv
import gzip
import io


def read_external_ids(gzipped_bytes: bytes) -> list[str]:
    """Decompress a gzipped CSV and return unique external customer IDs.

    The CSV is expected to have a header row including a `customer_id` column.
    Rows are deduplicated (a customer may legitimately appear more than once in
    a source file) while preserving first-seen order, which keeps output stable
    for tests and for the parquet file that follows.

    Blank customer_id cells are skipped. A missing customer_id column raises
    ValueError -- that is a malformed file, not an empty one, and should fail
    loudly rather than silently produce zero members.
    """
    text = gzip.decompress(gzipped_bytes).decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None or "customer_id" not in reader.fieldnames:
        raise ValueError(
            "audience CSV missing required 'customer_id' column; "
            f"found columns: {reader.fieldnames}"
        )

    seen: set[str] = set()
    ordered: list[str] = []
    for row in reader:
        cid = (row.get("customer_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ordered.append(cid)
    return ordered
