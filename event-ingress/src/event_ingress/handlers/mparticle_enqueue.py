"""Lambda entry point: mparticle-enqueue.

Receives the raw mParticle Firehose POST, validates the API key, enriches the
batch with the resolved tenant_id, and puts it to the mparticle-enqueue Kinesis
stream for async processing.

Why async? The mParticle Firehose has a tight response timeout (~10 seconds).
We acknowledge immediately and let mparticle-processing do the heavy work
(MCI resolution, format conversion, routing to internal streams) asynchronously
from the Kinesis trigger.

Auth: same apikey-metadata DynamoDB table as joust, but this Lambda runs behind
a plain API Gateway method (no Lambda authorizer). It reads account_settings.apiKey
from the mParticle payload and looks it up directly via ApiKeyCache.

Input (HTTP POST body from mParticle Firehose):
  {
    "type": "event_processing_request" | "audience_membership_change_request",
    "id": "<batch-id>",
    "timestamp_ms": <epoch-ms>,
    "account": {"account_settings": {"apiKey": "<key>"}},
    "user_identities": [...],
    "user_attributes": {...},
    "events": [...]
  }

Output: HTTP 200 immediately. The enriched batch (with tenant_id added) goes
to the mparticle-enqueue Kinesis stream, partitioned by tenant_id.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from event_ingress.auth.api_key_cache import ApiKeyCache
from event_ingress.kinesis.producer import put_records_batch

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

API_KEY_TABLE_NAME = os.environ.get("API_KEY_TABLE_NAME", "")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "900"))
MPARTICLE_ENQUEUE_STREAM = os.environ.get("MPARTICLE_ENQUEUE_STREAM", "")

_cache: ApiKeyCache | None = None
_kinesis_client: Any = None


def _get_cache() -> ApiKeyCache:
    global _cache
    if _cache is None:
        _cache = ApiKeyCache(table_name=API_KEY_TABLE_NAME, ttl_seconds=CACHE_TTL_SECONDS)
    return _cache


def _get_kinesis():
    global _kinesis_client
    if _kinesis_client is None:
        import boto3

        _kinesis_client = boto3.client("kinesis")
    return _kinesis_client


def _ok() -> dict:
    return {"statusCode": 200, "body": ""}


def _bad(message: str) -> dict:
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def handler(event: dict, context=None) -> dict:
    """Receive mParticle Firehose POST, enrich with tenant_id, put to Kinesis."""
    raw_body = event.get("body") or "{}"
    try:
        payload: dict = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("mparticle-enqueue: invalid JSON body")
        return _bad("Invalid JSON")

    # Extract the API key mParticle puts in account_settings.
    api_key: str = payload.get("account", {}).get("account_settings", {}).get("apiKey", "")
    if not api_key:
        logger.warning("mparticle-enqueue: missing account_settings.apiKey")
        return _ok()  # Return 200 to avoid mParticle retry storms on config errors.

    tenant_id = _get_cache().get_tenant_id(api_key)
    if tenant_id is None:
        logger.warning("mparticle-enqueue: unknown or disabled API key, skipping batch")
        return _ok()  # 200 so mParticle does not retry; we intentionally drop it.

    # Enrich the payload with tenant_id and normalise to our internal field names.
    enriched = {
        "tenant_id": tenant_id,
        "batch_id": payload.get("id", ""),
        "timestamp_ms": payload.get("timestamp_ms", 0),
        "message_type": payload.get("type", ""),
        "user_identities": payload.get("user_identities", []),
        "user_attributes": payload.get("user_attributes", {}),
        "events": payload.get("events", []),
        "raw": payload,
    }

    try:
        put_records_batch(
            stream_name=MPARTICLE_ENQUEUE_STREAM,
            records=[
                {
                    "Data": json.dumps(enriched).encode("utf-8"),
                    "PartitionKey": tenant_id,
                }
            ],
            kinesis_client=_get_kinesis(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("mparticle-enqueue: Kinesis put failed: %s", exc)
        # Still return 200 to mParticle to prevent retry loops.
        # The failure is surfaced via CloudWatch alarms.
        return _ok()

    logger.info(
        "mparticle-enqueue: queued batch_id=%s type=%s tenant=%s",
        enriched["batch_id"],
        enriched["message_type"],
        tenant_id,
    )
    return _ok()
