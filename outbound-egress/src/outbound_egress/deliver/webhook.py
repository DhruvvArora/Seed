"""Webhook delivery.

Placeholder for commit 5 (Delivery layer). Will define:
  - deliver_webhook(destination, payload) -> DeliveryResult
  - POST or PUT per destination config, 10 second timeout
  - 5xx -> raise a retryable error (caller retries, up to 3 attempts,
    1 second backoff)
  - 4xx -> raise a non-retryable error (misconfigured connector, needs
    operator attention, do not retry)
"""
