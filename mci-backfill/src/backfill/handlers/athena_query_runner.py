"""Lambda entry point: athena-query-runner.

Runs one of the pipeline's two typed Athena queries to completion and returns
typed JSON, so the Step Function never has to parse Athena's CSV-grid result
shape or its bracketed ARRAY_AGG string format through ASL intrinsics.

This is deliberately not a general SQL gateway. It accepts exactly two named
operations, "min_max_row_number" and "customer_ids_batch", matching
mci-backfill/athena/02_*.sql and 03_*.sql. Keeping the contract closed (fixed
inputs, fixed output shapes) is what keeps this Lambda's tests, and its
behavior, predictable. The CTAS and DROP queries need no typed results back
(just completion), so they stay as plain Athena SDK integration states in the
ASL and never come through here.

Input (min_max_row_number):
  {"operation": "min_max_row_number", "temp_table": str, "tenant_id": str,
   "athena_database": str, "athena_output_location": str}
Output: {"min_row": int, "max_row_num": int, "row_count": int}

Input (customer_ids_batch):
  {"operation": "customer_ids_batch", "temp_table": str, "tenant_id": str,
   "start": int, "end": int, "athena_database": str, "athena_output_location": str}
Output: {"customer_ids": [str, ...]}
"""

from __future__ import annotations

import os

import boto3

from backfill.athena.client import get_customer_ids_batch, get_min_max_row_number

ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "backfill-workgroup")


def handler(event: dict, context=None) -> dict:
    athena_client = boto3.client("athena")
    operation = event["operation"]

    if operation == "min_max_row_number":
        return get_min_max_row_number(
            athena_client,
            temp_table=event["temp_table"],
            tenant_id=event["tenant_id"],
            database=event["athena_database"],
            workgroup=ATHENA_WORKGROUP,
            output_location=event["athena_output_location"],
        )

    if operation == "customer_ids_batch":
        return get_customer_ids_batch(
            athena_client,
            temp_table=event["temp_table"],
            tenant_id=event["tenant_id"],
            start=int(event["start"]),
            end=int(event["end"]),
            database=event["athena_database"],
            workgroup=ATHENA_WORKGROUP,
            output_location=event["athena_output_location"],
        )

    raise ValueError(f"Unknown operation: {operation!r}")
