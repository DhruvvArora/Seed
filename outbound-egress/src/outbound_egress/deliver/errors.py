"""Shared exception types for all three delivery paths (webhook, kinesis,
oauth), so aqueduct-distributor's retry loop (commit 6) can handle any
destination type the same way, without knowing which one it is.

Both carry an optional status_code, so the distributor can write an
accurate connection_status (HTTP response code and timestamp) even when
delivery ultimately fails, not just on success. status_code is None for
failures with no HTTP response at all (timeouts, connection errors, STS
failures, jq transformation errors upstream of any delivery attempt).
"""

from __future__ import annotations


class DeliveryError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RetryableDeliveryError(DeliveryError):
    """Delivery failed in a way the caller should retry: a 5xx response, a
    Kinesis throughput-exceeded error, a network timeout, or a transient
    connection failure. The distributor retries up to 3 times."""


class NonRetryableDeliveryError(DeliveryError):
    """Delivery failed in a way that will not succeed on retry: a 4xx
    response. This indicates a misconfigured connector that needs operator
    attention, not a transient problem, so the distributor does not retry."""
