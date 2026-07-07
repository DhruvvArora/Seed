"""Shared exception types for all three delivery paths (webhook, kinesis,
oauth), so aqueduct-distributor's retry loop (commit 6) can handle any
destination type the same way, without knowing which one it is.
"""

from __future__ import annotations


class RetryableDeliveryError(Exception):
    """Delivery failed in a way the caller should retry: a 5xx response, a
    Kinesis throughput-exceeded error, a network timeout, or a transient
    connection failure. The distributor retries up to 3 times."""


class NonRetryableDeliveryError(Exception):
    """Delivery failed in a way that will not succeed on retry: a 4xx
    response. This indicates a misconfigured connector that needs operator
    attention, not a transient problem, so the distributor does not retry."""
