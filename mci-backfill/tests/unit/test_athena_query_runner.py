"""Unit tests for the athena-query-runner Lambda handler.

The handler itself has no logic beyond dispatch, so these tests confirm it
routes to the right client function with the right arguments (via monkeypatch)
and rejects anything outside its two named operations. The query mechanics
themselves are covered in test_athena_client.py.
"""

from __future__ import annotations

import pytest

import backfill.handlers.athena_query_runner as runner


def test_dispatches_min_max_row_number(monkeypatch):
    captured = {}

    def fake_get_min_max(client, **kwargs):
        captured.update(kwargs)
        return {"min_row": 0, "max_row_num": 9, "row_count": 10}

    monkeypatch.setattr(runner, "get_min_max_row_number", fake_get_min_max)
    monkeypatch.setattr(runner.boto3, "client", lambda name: object())

    out = runner.handler(
        {
            "operation": "min_max_row_number",
            "temp_table": "temp_t",
            "tenant_id": "t1",
            "athena_database": "db",
            "athena_output_location": "s3://out/",
        }
    )

    assert out == {"min_row": 0, "max_row_num": 9, "row_count": 10}
    assert captured["tenant_id"] == "t1"
    assert captured["temp_table"] == "temp_t"


def test_dispatches_customer_ids_batch(monkeypatch):
    captured = {}

    def fake_get_batch(client, **kwargs):
        captured.update(kwargs)
        return {"customer_ids": ["a", "b"]}

    monkeypatch.setattr(runner, "get_customer_ids_batch", fake_get_batch)
    monkeypatch.setattr(runner.boto3, "client", lambda name: object())

    out = runner.handler(
        {
            "operation": "customer_ids_batch",
            "temp_table": "temp_t",
            "tenant_id": "t1",
            "start": 0,
            "end": 9999,
            "athena_database": "db",
            "athena_output_location": "s3://out/",
        }
    )

    assert out == {"customer_ids": ["a", "b"]}
    assert captured["start"] == 0
    assert captured["end"] == 9999


def test_rejects_unknown_operation(monkeypatch):
    monkeypatch.setattr(runner.boto3, "client", lambda name: object())
    with pytest.raises(ValueError, match="Unknown operation"):
        runner.handler({"operation": "arbitrary_sql", "query": "DROP TABLE x"})
