"""Lambda entry point: backfill-iteration-utility.

Pure arithmetic helper that drives the Step Function's per-tenant iterator loop.
Given the current offset and batch step, it returns the next [start, end] range
and whether the tenant is done. No AWS calls; trivially unit-testable.

Input  : {"current_offset": int, "batch_step": int, "max_row": int}
Output : {"start": int, "end": int, "next_offset": int, "is_done": bool}

TODO: compute start=current_offset, end=current_offset+batch_step-1 (clamped),
next_offset, and is_done = next_offset > max_row.
"""

from __future__ import annotations


def handler(event: dict, context=None) -> dict:
    raise NotImplementedError
