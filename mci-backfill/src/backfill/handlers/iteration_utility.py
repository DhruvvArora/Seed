"""Lambda entry point: backfill-iteration-utility.

Pure arithmetic helper that drives the Step Function's per-tenant iterator loop.
Given where we are (current_offset) and how big each batch is (batch_step), it
returns the next [start, end] range to query from Athena and whether this tenant
is finished. It makes no AWS calls, which is why it is trivially unit-testable
and the right first Lambda to build.

Row-number contract (see the note below): offsets are 0-indexed and `max_row`
is the total row count for the tenant, i.e. an EXCLUSIVE upper bound over rows
[0, max_row). This is fixed by the spec's two named tests:

    FirstBatch: (offset=0,      step=10000, max=840000) -> start=0,      end=9999
    LastBatch:  (offset=830000, step=10000, max=840000) -> start=830000, end=839999, is_done=True

`is_done` becomes True once the NEXT offset would reach the count, so the loop
processes the final (possibly short) batch and then stops. The upstream Athena
CTAS must therefore emit 0-indexed row numbers (`ROW_NUMBER() OVER (...) - 1`)
and pass the row count as `max_row`, so the SQL and this Lambda agree.

Input  : {"current_offset": int, "batch_step": int, "max_row": int}
Output : {"start": int, "end": int, "next_offset": int, "is_done": bool}
"""

from __future__ import annotations


def next_batch(current_offset: int, batch_step: int, max_row: int) -> dict[str, object]:
    """Compute the next batch range and whether the tenant is exhausted.

    `end` is clamped to the last valid row index (max_row - 1) so a non-multiple
    final batch stays tight rather than over-reaching past the data. The Athena
    BETWEEN query is bounded by real rows anyway, but a clamped range is honest
    about what the batch actually covers.
    """
    if batch_step <= 0:
        raise ValueError(f"batch_step must be positive, got {batch_step}")

    start = current_offset
    # Clamp so the final short batch reports the true last index, not offset+step-1.
    end = min(current_offset + batch_step - 1, max_row - 1)
    next_offset = current_offset + batch_step
    is_done = next_offset >= max_row

    return {"start": start, "end": end, "next_offset": next_offset, "is_done": is_done}


def handler(event: dict, context=None) -> dict[str, object]:
    """Step Function task entry point. Thin wrapper over `next_batch`."""
    return next_batch(
        current_offset=int(event["current_offset"]),
        batch_step=int(event["batch_step"]),
        max_row=int(event["max_row"]),
    )
