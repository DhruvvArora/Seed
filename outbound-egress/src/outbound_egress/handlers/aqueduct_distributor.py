"""aqueduct-distributor: Kinesis consumer for the plague stream.

For each BatchProgress message:
  1. Parse the batch, iterate the progresses array
  2. For each progress entry, batch-load the connector rows named in its
     connectors list
  3. For each connector: decrypt destination via KMS (using a DecryptCache
     created fresh in this handler invocation, never at module scope, see
     crypto/kms.py), apply the JQ transform if configured, deliver, then
     write connection_status back to DynamoDB

Concurrency:
  - All connectors for a single progress event: ThreadPoolExecutor
  - Progress events within a batch: processed sequentially, to preserve
    per-customer ordering

Retries: RetryableDeliveryError triggers up to 3 attempts total (the spec's
webhook 5xx and kinesis throughput-exceeded retry counts), with a 1 second
backoff between attempts, applied the same way regardless of destination
type since all three delivery paths raise the same shared error type.
NonRetryableDeliveryError and TransformationError are not retried.

Failure isolation: a connector delivery failure, of any kind, after retries
are exhausted, is written to connection_status and logged. It never raises
out of this handler. Only truly unexpected errors (a Kinesis record that
will not even parse, an unhandled exception processing a progress entry)
are logged and skipped per-record/per-progress, so a systemic problem still
surfaces via CloudWatch error alarms without one bad record poisoning
delivery for everything else in the batch.

Kinesis trigger config (Terraform): batch size 100, max 3 retries,
bisect-on-error enabled. That is Lambda's batch-level retry for the trigger
itself; it is separate from, and on top of, the per-connector delivery
retries described above.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from outbound_egress.crypto.kms import DecryptCache, DestinationCrypto
from outbound_egress.deliver.errors import NonRetryableDeliveryError, RetryableDeliveryError
from outbound_egress.deliver.kinesis import deliver_kinesis
from outbound_egress.deliver.oauth import OAuthTokenCache, deliver_oauth
from outbound_egress.deliver.webhook import deliver_webhook
from outbound_egress.model.types import (
    Connector,
    DeliveryResult,
    JwtOAuthDestination,
    KinesisDestination,
    Progress,
    WebhookDestination,
    parse_destination,
)
from outbound_egress.progress.parser import decode_kinesis_record
from outbound_egress.store.connectors import ConnectorStore
from outbound_egress.store.transformations import TransformationStore
from outbound_egress.transform.jq_transform import TransformationError, apply_transformation

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CONNECTORS_TABLE_NAME = os.environ.get("CONNECTORS_TABLE_NAME", "")
TRANSFORMATIONS_TABLE_NAME = os.environ.get("TRANSFORMATIONS_TABLE_NAME", "")

MAX_DELIVERY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
CONNECTOR_FANOUT_MAX_WORKERS = 10

# Safe at module scope, per Lambda container: boto3-backed stores and the
# KMS client wrapper carry no per-invocation state, and OAuthTokenCache's
# staleness is bounded by each token's own expiry (see deliver/oauth.py).
# DecryptCache is deliberately NOT here; see handler() below.
_connector_store: ConnectorStore | None = None
_transformation_store: TransformationStore | None = None
_destination_crypto: DestinationCrypto | None = None
_oauth_token_cache = OAuthTokenCache()


def _get_connector_store() -> ConnectorStore:
    global _connector_store
    if _connector_store is None:
        _connector_store = ConnectorStore(CONNECTORS_TABLE_NAME)
    return _connector_store


def _get_transformation_store() -> TransformationStore:
    global _transformation_store
    if _transformation_store is None:
        _transformation_store = TransformationStore(TRANSFORMATIONS_TABLE_NAME)
    return _transformation_store


def _get_destination_crypto() -> DestinationCrypto:
    global _destination_crypto
    if _destination_crypto is None:
        _destination_crypto = DestinationCrypto()
    return _destination_crypto


def _deliver_with_retry(
    deliver_fn: Callable[[], DeliveryResult], connector_name: str
) -> DeliveryResult:
    last_exc: RetryableDeliveryError | None = None
    for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
        try:
            return deliver_fn()
        except RetryableDeliveryError as exc:
            last_exc = exc
            logger.warning(
                "aqueduct-distributor: retryable delivery error for connector=%s attempt=%d/%d: %s",
                connector_name,
                attempt,
                MAX_DELIVERY_ATTEMPTS,
                exc,
            )
            if attempt < MAX_DELIVERY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS)

    assert last_exc is not None  # loop always sets this before falling through
    raise last_exc


def _deliver_to_connector(
    connector: Connector,
    tenant_id: str,
    customer_id: str,
    progress_payload: dict[str, Any],
    decrypt_cache: DecryptCache,
) -> None:
    """Decrypt, transform, and deliver to one connector.

    Never raises: any failure (transformation, decrypt, or delivery, after
    retries) is caught, logged, and written to connection_status, so one bad
    connector never blocks the others in the ThreadPoolExecutor fan-out.
    """
    if not connector.enabled:
        logger.info("aqueduct-distributor: connector=%s disabled, skipping", connector.name)
        return

    result: DeliveryResult
    try:
        crypto = _get_destination_crypto()
        destination_json = decrypt_cache.get_or_decrypt(
            crypto, tenant_id, connector.name, connector.destination
        )
        try:
            destination = parse_destination(connector.destination_type, destination_json)
        except ValueError as exc:
            raise NonRetryableDeliveryError(
                f"connector={connector.name} has an invalid destination config: {exc}"
            ) from exc

        jq_script = None
        if connector.transformation_name:
            transformation = _get_transformation_store().get_transformation(
                tenant_id, connector.transformation_name
            )
            jq_script = transformation.jq_script if transformation else None

        payload = apply_transformation(jq_script, progress_payload)

        if isinstance(destination, WebhookDestination):
            result = _deliver_with_retry(
                lambda: deliver_webhook(destination, payload), connector.name
            )
        elif isinstance(destination, KinesisDestination):
            result = _deliver_with_retry(
                lambda: deliver_kinesis(destination, tenant_id, customer_id, payload),
                connector.name,
            )
        elif isinstance(destination, JwtOAuthDestination):
            result = _deliver_with_retry(
                lambda: deliver_oauth(
                    destination, tenant_id, connector.name, payload, _oauth_token_cache
                ),
                connector.name,
            )
        else:
            raise NonRetryableDeliveryError(
                f"connector={connector.name} resolved to an unhandled destination type"
            )
    except TransformationError as exc:
        logger.error(
            "aqueduct-distributor: transformation failed for connector=%s: %s",
            connector.name,
            exc,
        )
        result = DeliveryResult(success=False, status_code=None, error=str(exc))
    except (RetryableDeliveryError, NonRetryableDeliveryError) as exc:
        logger.error(
            "aqueduct-distributor: delivery failed for connector=%s: %s",
            connector.name,
            exc,
        )
        result = DeliveryResult(success=False, status_code=exc.status_code, error=str(exc))

    _get_connector_store().update_connection_status(
        tenant_id, connector.name, result.to_connection_status()
    )


def _process_progress(progress: Progress, decrypt_cache: DecryptCache) -> None:
    """Fan out to every connector named on one progress event, concurrently."""
    if not progress.connectors:
        return

    connectors = _get_connector_store().get_connectors(progress.tenant_id, progress.connectors)

    missing = set(progress.connectors) - set(connectors.keys())
    for name in missing:
        logger.warning(
            "aqueduct-distributor: connector=%s referenced by progress but not found for tenant=%s",
            name,
            progress.tenant_id,
        )

    if not connectors:
        return

    payload = progress.to_dict()

    with ThreadPoolExecutor(max_workers=min(CONNECTOR_FANOUT_MAX_WORKERS, len(connectors))) as pool:
        futures = [
            pool.submit(
                _deliver_to_connector,
                connector,
                progress.tenant_id,
                progress.internal_customer_id,
                payload,
                decrypt_cache,
            )
            for connector in connectors.values()
        ]
        for future in as_completed(futures):
            future.result()  # re-raise anything _deliver_to_connector did not itself catch


def handler(event: dict[str, Any], context: Any = None) -> None:
    """Kinesis trigger handler for the plague stream."""
    # Created fresh, once per invocation, on purpose. Never hoist this to
    # module scope: see crypto/kms.py for why a stale decrypted destination
    # is a silent-misdelivery risk that an invocation-scoped cache avoids.
    decrypt_cache = DecryptCache()

    for record in event.get("Records", []):
        try:
            batch = decode_kinesis_record(record)
        except Exception as exc:  # noqa: BLE001
            logger.error("aqueduct-distributor: failed to parse BatchProgress record: %s", exc)
            continue

        for progress in batch.progresses:
            try:
                _process_progress(progress, decrypt_cache)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "aqueduct-distributor: unhandled error processing progress for "
                    "tenant=%s offer=%s: %s",
                    progress.tenant_id,
                    progress.offer_id,
                    exc,
                )
