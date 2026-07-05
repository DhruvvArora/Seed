"""Unit tests for the backfill iterator utility.

Covers the spec's two named cases (FirstBatch, LastBatch) plus the edges that
matter for loop correctness: a middle batch, a non-multiple final batch, the
done/not-done boundary, and the handler's input parsing and output shape.
"""

from __future__ import annotations

import pytest

from backfill.handlers.iteration_utility import handler, next_batch

STEP = 10000


def test_first_batch():
    """Spec: offset=0, step=10000, max=840000 -> start=0, end=9999."""
    result = next_batch(current_offset=0, batch_step=STEP, max_row=840000)
    assert result["start"] == 0
    assert result["end"] == 9999
    assert result["next_offset"] == 10000
    assert result["is_done"] is False  # 839,990 rows still to go


def test_last_batch():
    """Spec: offset=830000, step=10000, max=840000 -> end=839999, is_done=True."""
    result = next_batch(current_offset=830000, batch_step=STEP, max_row=840000)
    assert result["start"] == 830000
    assert result["end"] == 839999
    assert result["next_offset"] == 840000
    assert result["is_done"] is True


def test_middle_batch_is_not_done():
    result = next_batch(current_offset=10000, batch_step=STEP, max_row=840000)
    assert result["start"] == 10000
    assert result["end"] == 19999
    assert result["is_done"] is False


def test_non_multiple_final_batch_is_clamped():
    """A tenant whose count is not a multiple of the step: the final batch's
    end is clamped to the last real row index, and the loop terminates."""
    result = next_batch(current_offset=20000, batch_step=STEP, max_row=25000)
    assert result["start"] == 20000
    assert result["end"] == 24999  # clamped from 29999 to max_row - 1
    assert result["next_offset"] == 30000
    assert result["is_done"] is True


def test_single_batch_smaller_than_step():
    """A tiny tenant fits in one batch and is immediately done."""
    result = next_batch(current_offset=0, batch_step=STEP, max_row=42)
    assert result["start"] == 0
    assert result["end"] == 41
    assert result["is_done"] is True


def test_rejects_non_positive_step():
    with pytest.raises(ValueError, match="batch_step must be positive"):
        next_batch(current_offset=0, batch_step=0, max_row=100)


def test_handler_parses_event_and_returns_full_shape():
    """The Lambda handler reads the three int fields and returns all four."""
    out = handler({"current_offset": 0, "batch_step": STEP, "max_row": 840000})
    assert set(out.keys()) == {"start", "end", "next_offset", "is_done"}
    assert out["start"] == 0
    assert out["end"] == 9999
