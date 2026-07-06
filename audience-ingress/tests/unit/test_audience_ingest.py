"""Tests for the audience-ingest Step Function task."""

from __future__ import annotations

import gzip

from audience_ingress.handlers import audience_ingest


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 (boto3 kwarg names)
        return {"Body": _FakeBody(self._data)}


def _gzip_ids(n: int) -> bytes:
    lines = ["customer_id,audience_id"]
    lines += [f"cust-{i},aud-a" for i in range(n)]
    return gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))


def test_audience_ingest_mci_batching(monkeypatch) -> None:
    # 12,500 unique ids -> 3 MCI invokes: 5000 + 5000 + 2500.
    monkeypatch.setattr(audience_ingest, "_get_s3", lambda: _FakeS3(_gzip_ids(12_500)))

    call_sizes: list[int] = []

    def fake_resolve(function_name, customer_keys, *, qualifier, read_only=False):
        call_sizes.append(len(customer_keys))
        return {"tenant-abc": {k.customer_id: f"int-{k.customer_id}" for k in customer_keys}}

    monkeypatch.setattr(audience_ingest, "resolve_internal_ids", fake_resolve)

    out = audience_ingest.handler(
        {
            "s3_bucket": "dev-audience-uploads",
            "s3_key": "tenant-abc/aud-a/2026-07-05.csv.gz",
            "tenant_id": "tenant-abc",
            "audience_id": "aud-a",
        }
    )

    assert call_sizes == [5000, 5000, 2500]
    assert out["count"] == 12_500
    assert len(out["internal_customer_ids"]) == 12_500
    assert out["internal_customer_ids"][0] == "int-cust-0"
    assert out["external_ids_not_found"] == 0


def test_audience_ingest_counts_unresolved(monkeypatch) -> None:
    # MCI returns a mapping for only some ids; the rest count as not found.
    monkeypatch.setattr(audience_ingest, "_get_s3", lambda: _FakeS3(_gzip_ids(3)))

    def partial_resolve(function_name, customer_keys, *, qualifier, read_only=False):
        # Resolve only cust-0.
        return {"tenant-abc": {"cust-0": "int-cust-0"}}

    monkeypatch.setattr(audience_ingest, "resolve_internal_ids", partial_resolve)

    out = audience_ingest.handler(
        {
            "s3_bucket": "b",
            "s3_key": "k",
            "tenant_id": "tenant-abc",
            "audience_id": "aud-a",
        }
    )
    assert out["count"] == 1
    assert out["external_ids_not_found"] == 2
