"""Tests for outbound_egress.deliver.webhook."""

import pytest
import requests
import responses

from outbound_egress.deliver.errors import NonRetryableDeliveryError, RetryableDeliveryError
from outbound_egress.deliver.webhook import deliver_webhook
from outbound_egress.model.types import METHOD_POST, WebhookDestination


def _destination(url: str = "https://example.com/hook") -> WebhookDestination:
    return WebhookDestination(url=url, method=METHOD_POST, headers={"X-Api-Key": "secret"})


@responses.activate
def test_webhook_delivery_succeeds_on_200():
    responses.add(responses.POST, "https://example.com/hook", status=200, json={"ok": True})

    result = deliver_webhook(_destination(), {"offer_id": "offer-1"})

    assert result.success is True
    assert result.status_code == 200


@responses.activate
def test_webhook_delivery_sends_configured_headers_and_body():
    responses.add(responses.POST, "https://example.com/hook", status=200)

    deliver_webhook(_destination(), {"offer_id": "offer-1"})

    sent = responses.calls[0].request
    assert sent.headers["X-Api-Key"] == "secret"
    assert sent.body is not None
    assert b"offer-1" in sent.body


@responses.activate
def test_webhook_delivery_returns_retryable_error_on_503():
    responses.add(responses.POST, "https://example.com/hook", status=503)

    with pytest.raises(RetryableDeliveryError):
        deliver_webhook(_destination(), {"offer_id": "offer-1"})


@responses.activate
def test_webhook_delivery_returns_non_retryable_error_on_400():
    responses.add(responses.POST, "https://example.com/hook", status=400)

    with pytest.raises(NonRetryableDeliveryError):
        deliver_webhook(_destination(), {"offer_id": "offer-1"})


@responses.activate
def test_webhook_delivery_times_out_after_10_seconds_raises_retryable():
    responses.add(
        responses.POST,
        "https://example.com/hook",
        body=requests.exceptions.Timeout("simulated slow server"),
    )

    with pytest.raises(RetryableDeliveryError, match="timed out"):
        deliver_webhook(_destination(), {"offer_id": "offer-1"})
