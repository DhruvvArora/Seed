"""Kinesis PutRecords producer with retry for throttled records.

PutRecords is a best-effort API: it returns HTTP 200 even when some records
fail. Each record in the response has its own ErrorCode. Records with
ErrorCode "ProvisionedThroughputExceededException" or "InternalFailure" must
be retried. We do this with exponential backoff up to MAX_ATTEMPTS times.

Partition key convention: tenant_id#internal_customer_id
This groups all events for a customer onto the same shard so that ordered
processing is possible downstream (one shard = strict FIFO within the key).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, cast

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5
RETRYABLE_ERROR_CODES = {"ProvisionedThroughputExceededException", "InternalFailure"}


def build_partition_key(tenant_id: str, internal_customer_id: str) -> str:
    """Groups events for the same customer onto the same shard."""
    return f"{tenant_id}#{internal_customer_id}"


def put_event(
    stream_name: str,
    event_dict: dict,
    partition_key: str,
    kinesis_client: Any = None,
) -> None:
    """Put a single event dict to Kinesis with retry on throttle.

    Serializes the dict to JSON bytes, then delegates to put_records_batch.
    """
    put_records_batch(
        stream_name=stream_name,
        records=[{"Data": json.dumps(event_dict).encode("utf-8"), "PartitionKey": partition_key}],
        kinesis_client=kinesis_client,
    )


def put_records_batch(
    stream_name: str,
    records: list[dict],
    kinesis_client: Any = None,
) -> None:
    """Put a batch of pre-built Kinesis record dicts, retrying throttled records.

    Each record must be a dict with 'Data' (bytes) and 'PartitionKey' (str).
    Raises RuntimeError if any records fail after MAX_ATTEMPTS retries.
    """
    client = kinesis_client or boto3.client("kinesis")
    pending = records
    attempt = 0

    while pending and attempt < MAX_ATTEMPTS:
        if attempt > 0:
            sleep = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.info(
                "kinesis retry attempt %d for %d records on %s (sleeping %.1fs)",
                attempt,
                len(pending),
                stream_name,
                sleep,
            )
            time.sleep(sleep)

        response = client.put_records(StreamName=stream_name, Records=cast(Any, pending))
        failed_count = response.get("FailedRecordCount", 0)

        if failed_count == 0:
            return

        # Collect only the retryable failures for the next attempt.
        next_pending: list[dict] = []
        for i, result in enumerate(response["Records"]):
            error_code = result.get("ErrorCode", "")
            if error_code in RETRYABLE_ERROR_CODES:
                next_pending.append(pending[i])
            elif error_code:
                raise RuntimeError(
                    f"Kinesis PutRecords non-retryable error on stream {stream_name!r}: "
                    f"{error_code} -- {result.get('ErrorMessage', '')}"
                )

        pending = next_pending
        attempt += 1

    if pending:
        raise RuntimeError(
            f"Kinesis PutRecords: {len(pending)} record(s) still failing after "
            f"{MAX_ATTEMPTS} attempts on stream {stream_name!r}"
        )
