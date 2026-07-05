"""Validation rules for inbound events.

All validation is pure Python -- no AWS calls, no I/O -- so every rule is
trivially unit-testable without mocks.

Rules (from the spec):
  - event_id: present and non-empty
  - customer_id: present and non-empty
  - event_time: present and parseable as a naive ISO datetime (no TZ offset)
  - event_type: one of "transaction" or "action"
  - transaction.total: positive integer when event_type == "transaction"

A failed validation raises EventValidationError with a human-readable message.
The handler catches this and returns HTTP 400.
"""

from __future__ import annotations

from datetime import datetime

VALID_EVENT_TYPES = {"transaction", "action"}


class EventValidationError(ValueError):
    """Raised when an inbound event fails a validation rule."""


def validate_event(body: dict) -> None:
    """Validate all required fields of an inbound event.

    Raises EventValidationError on the first failing rule.
    """
    _require_non_empty(body, "event_id")
    _require_non_empty(body, "customer_id")
    _require_valid_datetime(body, "event_time")
    _require_event_type(body)

    if body.get("event_type") == "transaction":
        _require_positive_int_total(body)


# ---------------------------------------------------------------------------
# Individual rule checkers
# ---------------------------------------------------------------------------


def _require_non_empty(body: dict, field: str) -> None:
    value = body.get(field)
    if not value or not str(value).strip():
        raise EventValidationError(f"'{field}' is required and must be non-empty")


def _require_valid_datetime(body: dict, field: str) -> None:
    value = body.get(field)
    if not value:
        raise EventValidationError(f"'{field}' is required")
    # Spec: local time, no timezone. We accept ISO 8601 naive datetimes only.
    # Reject values with timezone offsets (Z, +HH:MM) to stay consistent with
    # the documented assumption that all inputs are treated as local time.
    raw = str(value)
    if raw.endswith("Z") or "+" in raw[10:] or (raw.count("-") > 2):
        raise EventValidationError(
            f"'{field}' must be a naive local datetime (no timezone offset); got: {raw!r}"
        )
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            datetime.strptime(raw, fmt)
            return
        except ValueError:
            continue
    raise EventValidationError(
        f"'{field}' is not a valid naive datetime; expected ISO 8601 local time, got: {raw!r}"
    )


def _require_event_type(body: dict) -> None:
    event_type = body.get("event_type")
    if event_type not in VALID_EVENT_TYPES:
        raise EventValidationError(
            f"'event_type' must be one of {sorted(VALID_EVENT_TYPES)}; got: {event_type!r}"
        )


def _require_positive_int_total(body: dict) -> None:
    transaction = body.get("transaction")
    if not isinstance(transaction, dict):
        raise EventValidationError(
            "'transaction' object is required when event_type is 'transaction'"
        )
    total = transaction.get("total")
    if not isinstance(total, int) or total <= 0:
        raise EventValidationError(
            f"'transaction.total' must be a positive integer (cents); got: {total!r}"
        )
