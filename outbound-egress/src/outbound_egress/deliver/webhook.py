"""Webhook delivery: POST or PUT a transformed payload to a connector's URL.

Per-request timeout is 10 seconds. Server-side errors (5xx) are treated as
retryable, since they usually indicate a transient problem on the brand's
side. Client errors (4xx) are treated as non-retryable, since they indicate
a misconfigured connector (wrong URL, bad auth header, malformed payload the
destination rejects) that needs operator attention, not another attempt.
Network-level failures (timeout, connection refused, DNS failure) are also
treated as retryable, since those are typically transient too.
"""

from __future__ import annotations

from typing import Any

import requests

from outbound_egress.deliver.errors import NonRetryableDeliveryError, RetryableDeliveryError
from outbound_egress.model.types import DeliveryResult, WebhookDestination

WEBHOOK_TIMEOUT_SECONDS = 10.0


def deliver_webhook(
    destination: WebhookDestination,
    payload: dict[str, Any],
    http: Any = None,
) -> DeliveryResult:
    """Deliver payload to destination.url using destination.method and headers.

    `http` accepts an injected requests-like session for testing (the
    `responses` library patches the real `requests` module directly, so
    tests can also just call this with the default and use `responses`
    decorators; the parameter exists for cases that want an explicit fake).
    """
    client = http or requests

    try:
        response = client.request(
            destination.method,
            destination.url,
            json=payload,
            headers=destination.headers,
            timeout=WEBHOOK_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise RetryableDeliveryError(
            f"webhook request to {destination.url} timed out after {WEBHOOK_TIMEOUT_SECONDS}s"
        ) from exc
    except requests.RequestException as exc:
        raise RetryableDeliveryError(f"webhook request to {destination.url} failed: {exc}") from exc

    if 500 <= response.status_code < 600:
        raise RetryableDeliveryError(
            f"webhook to {destination.url} returned {response.status_code}",
            status_code=response.status_code,
        )

    if 400 <= response.status_code < 500:
        raise NonRetryableDeliveryError(
            f"webhook to {destination.url} returned {response.status_code}, "
            "connector needs operator attention",
            status_code=response.status_code,
        )

    return DeliveryResult(success=True, status_code=response.status_code)
