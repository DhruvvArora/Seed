"""Unit tests for the low-level Athena client (start, poll, fetch, parse).

A fake Athena client models the real two-step API shape: start_query_execution
returns an id, get_query_execution reports the state across successive polls
(RUNNING for a while, then a terminal state), and get_query_results returns
the CSV-grid shape (a header row plus data rows of {"VarCharValue": ...}
cells). Polling uses an injected no-op sleep_fn so tests run instantly and the
poll count is asserted directly, proving the ceiling is real and not just
documented.
"""

from __future__ import annotations

import pytest

from backfill.athena.client import (
    AthenaQueryFailed,
    AthenaQueryTimedOut,
    _parse_array_agg_cell,
    get_customer_ids_batch,
    get_min_max_row_number,
)


class _FakeAthenaClient:
    """States is the sequence of QueryExecution.Status.State values returned
    on successive get_query_execution calls (last one repeats if exhausted).
    result_rows is the ResultSet.Rows list returned by get_query_results."""

    def __init__(self, states: list[str], result_rows: list[list[str]] | None = None):
        self._states = states
        self._poll_count = 0
        self._result_rows = result_rows or []
        self.start_query_execution_calls: list[dict] = []

    def start_query_execution(self, **kwargs):
        self.start_query_execution_calls.append(kwargs)
        return {"QueryExecutionId": "qid-1"}

    def get_query_execution(self, QueryExecutionId):
        state = self._states[min(self._poll_count, len(self._states) - 1)]
        self._poll_count += 1
        resp = {"QueryExecution": {"Status": {"State": state}}}
        if state in ("FAILED", "CANCELLED"):
            resp["QueryExecution"]["Status"]["StateChangeReason"] = "simulated failure"
        return resp

    def get_query_results(self, QueryExecutionId):
        header = {"Data": [{"VarCharValue": "header"}]}
        rows = [header] + [
            {"Data": [{"VarCharValue": cell} for cell in row]} for row in self._result_rows
        ]
        return {"ResultSet": {"Rows": rows}}


def _noop_sleep(_seconds):
    pass


# ----- _parse_array_agg_cell --------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[cust1, cust2, cust3]", ["cust1", "cust2", "cust3"]),
        ("[cust1]", ["cust1"]),
        ("[]", []),
        ("", []),
        ("[  cust1 ,  cust2 ]", ["cust1", "cust2"]),
    ],
)
def test_parse_array_agg_cell(raw, expected):
    assert _parse_array_agg_cell(raw) == expected


# ----- get_min_max_row_number --------------------------------------------------


def test_min_max_returns_typed_ints_after_polling():
    client = _FakeAthenaClient(
        states=["QUEUED", "RUNNING", "SUCCEEDED"],
        result_rows=[["0", "3", "4"]],
    )
    result = get_min_max_row_number(
        client,
        temp_table="temp_t",
        tenant_id="t1",
        database="db",
        workgroup="wg",
        output_location="s3://out/",
        poll_interval_seconds=0,
        max_poll_attempts=10,
        sleep_fn=_noop_sleep,
    )
    assert result == {"min_row": 0, "max_row_num": 3, "row_count": 4}
    assert "tenant_id = 't1'" in client.start_query_execution_calls[0]["QueryString"]


def test_min_max_raises_on_failed_query():
    client = _FakeAthenaClient(states=["RUNNING", "FAILED"])
    with pytest.raises(AthenaQueryFailed, match="FAILED"):
        get_min_max_row_number(
            client,
            temp_table="temp_t",
            tenant_id="t1",
            database="db",
            workgroup="wg",
            output_location="s3://out/",
            poll_interval_seconds=0,
            max_poll_attempts=10,
            sleep_fn=_noop_sleep,
        )


def test_min_max_raises_on_timeout_with_explicit_ceiling():
    """A query that never leaves RUNNING must fail loudly once the poll budget
    (max_poll_attempts) is exhausted, not hang indefinitely."""
    client = _FakeAthenaClient(states=["RUNNING"])  # never terminal
    sleep_calls = []

    with pytest.raises(AthenaQueryTimedOut, match="poll budget exhausted"):
        get_min_max_row_number(
            client,
            temp_table="temp_t",
            tenant_id="t1",
            database="db",
            workgroup="wg",
            output_location="s3://out/",
            poll_interval_seconds=5,
            max_poll_attempts=4,
            sleep_fn=sleep_calls.append,
        )

    assert len(sleep_calls) == 4  # exactly the poll ceiling, not open-ended


# ----- get_customer_ids_batch --------------------------------------------------


def test_customer_ids_batch_parses_array_agg():
    client = _FakeAthenaClient(
        states=["SUCCEEDED"],
        result_rows=[["[ext-1, ext-2, ext-3]"]],
    )
    result = get_customer_ids_batch(
        client,
        temp_table="temp_t",
        tenant_id="t1",
        start=0,
        end=9999,
        database="db",
        workgroup="wg",
        output_location="s3://out/",
        poll_interval_seconds=0,
        max_poll_attempts=10,
        sleep_fn=_noop_sleep,
    )
    assert result == {"customer_ids": ["ext-1", "ext-2", "ext-3"]}
    sent_sql = client.start_query_execution_calls[0]["QueryString"]
    assert "BETWEEN 0 AND 9999" in sent_sql


def test_customer_ids_batch_empty_range_returns_empty_list():
    client = _FakeAthenaClient(states=["SUCCEEDED"], result_rows=[["[]"]])
    result = get_customer_ids_batch(
        client,
        temp_table="temp_t",
        tenant_id="t1",
        start=0,
        end=9999,
        database="db",
        workgroup="wg",
        output_location="s3://out/",
        poll_interval_seconds=0,
        max_poll_attempts=10,
        sleep_fn=_noop_sleep,
    )
    assert result == {"customer_ids": []}
