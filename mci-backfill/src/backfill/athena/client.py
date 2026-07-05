"""Low-level Athena query execution and typed result parsing.

Wraps the two-step Athena API shape (start_query_execution, poll, then
get_query_results) behind a narrow, typed interface. The Step Function only
ever sees typed JSON coming back from the athena-query-runner Lambda that sits
on top of this module; the CSV-grid VarCharValue cells and the bracketed
ARRAY_AGG string format ("[a, b, c]", not JSON) stay here.

This module intentionally does not accept arbitrary caller SQL. It only knows
how to run the two specific queries this pipeline needs (see
mci-backfill/athena/02 and 03_*.sql), so its inputs and outputs stay narrow and
its tests stay meaningful.
"""

from __future__ import annotations

import time


class AthenaQueryFailed(RuntimeError):
    """The query reached a terminal state other than SUCCEEDED."""


class AthenaQueryTimedOut(RuntimeError):
    """The query did not reach a terminal state within the polling budget.

    This is a deliberate, explicit ceiling: max_poll_attempts * poll_interval
    is the hard limit on how long this Lambda waits, so a stuck query fails
    loudly here instead of silently running until the Lambda's own timeout
    kills it.
    """


_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _execute_and_wait(
    athena_client,
    sql: str,
    database: str,
    workgroup: str,
    output_location: str,
    poll_interval_seconds: float,
    max_poll_attempts: int,
    sleep_fn=time.sleep,
) -> str:
    """Start the query and poll get_query_execution until it is terminal."""
    start_resp = athena_client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
        ResultConfiguration={"OutputLocation": output_location},
    )
    execution_id = start_resp["QueryExecutionId"]

    state = "UNKNOWN"
    for _ in range(max_poll_attempts):
        status_resp = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = status_resp["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return execution_id
        if state in _TERMINAL_STATES:  # FAILED or CANCELLED
            reason = status_resp["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise AthenaQueryFailed(f"Athena query {execution_id} ended in {state}: {reason}")
        sleep_fn(poll_interval_seconds)

    raise AthenaQueryTimedOut(
        f"Athena query {execution_id} still {state} after "
        f"{max_poll_attempts * poll_interval_seconds:.0f}s (poll budget exhausted)"
    )


def _parse_array_agg_cell(raw: str) -> list[str]:
    """Athena's GetQueryResults renders ARRAY_AGG as a bracketed,
    comma-space-separated string, e.g. "[cust1, cust2, cust3]", not JSON.
    An aggregation with no matching rows comes back as "[]"."""
    trimmed = raw.strip()
    if trimmed in ("[]", ""):
        return []
    inner = trimmed.removeprefix("[").removesuffix("]")
    return [item.strip() for item in inner.split(",") if item.strip()]


def get_min_max_row_number(
    athena_client,
    temp_table: str,
    tenant_id: str,
    database: str,
    workgroup: str,
    output_location: str,
    poll_interval_seconds: float = 2.0,
    max_poll_attempts: int = 30,
    sleep_fn=time.sleep,
) -> dict[str, int]:
    """Runs the min/max/count query (02_get_min_max_row_number.sql) for one
    tenant and returns typed ints.

    Returns {"min_row": int, "max_row_num": int, "row_count": int}. The
    iteration-utility Lambda's max_row parameter expects row_count (an
    exclusive count), not max_row_num. See mci-backfill/athena/README.md.
    """
    sql = (
        f"SELECT MIN(row_num) AS min_row, MAX(row_num) AS max_row_num, "
        f"COUNT(*) AS row_count FROM {temp_table} WHERE tenant_id = '{tenant_id}'"
    )
    execution_id = _execute_and_wait(
        athena_client,
        sql,
        database,
        workgroup,
        output_location,
        poll_interval_seconds,
        max_poll_attempts,
        sleep_fn,
    )
    results = athena_client.get_query_results(QueryExecutionId=execution_id)
    # Row 0 is the header; this aggregate query always produces exactly one data row.
    data_row = results["ResultSet"]["Rows"][1]["Data"]
    return {
        "min_row": int(data_row[0]["VarCharValue"]),
        "max_row_num": int(data_row[1]["VarCharValue"]),
        "row_count": int(data_row[2]["VarCharValue"]),
    }


def get_customer_ids_batch(
    athena_client,
    temp_table: str,
    tenant_id: str,
    start: int,
    end: int,
    database: str,
    workgroup: str,
    output_location: str,
    poll_interval_seconds: float = 2.0,
    max_poll_attempts: int = 30,
    sleep_fn=time.sleep,
) -> dict[str, list[str]]:
    """Runs the batch query (03_get_customer_ids_batch.sql) for one tenant's
    [start, end] range (both inclusive, 0-indexed).

    Returns {"customer_ids": [str, ...]}.
    """
    sql = (
        f"SELECT ARRAY_AGG(customer_id) AS customer_ids FROM {temp_table} "
        f"WHERE tenant_id = '{tenant_id}' AND row_num BETWEEN {start} AND {end}"
    )
    execution_id = _execute_and_wait(
        athena_client,
        sql,
        database,
        workgroup,
        output_location,
        poll_interval_seconds,
        max_poll_attempts,
        sleep_fn,
    )
    results = athena_client.get_query_results(QueryExecutionId=execution_id)
    data_row = results["ResultSet"]["Rows"][1]["Data"]
    raw_cell = data_row[0].get("VarCharValue", "[]")
    return {"customer_ids": _parse_array_agg_cell(raw_cell)}
