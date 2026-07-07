"""Cross-account Kinesis delivery.

Assumes the IAM role specified in the connector's destination config via
STS, then puts the transformed payload as a single record to the target
stream in the destination account and region. The partition key matches the
scheme used by the platform's internal streams: f"{tenant_id}#{customer_id}",
so downstream ordering-per-customer guarantees hold the same way externally
as they do internally.

A fresh set of assumed-role credentials, and a fresh Kinesis client built
from them, is created on every call. STS AssumeRole credentials are cheap to
mint and the assumed session is scoped to this one delivery; there is no
cross-invocation caching here the way there is for the KMS decrypt cache or
the OAuth token cache, since a delivery-scoped session is simpler and STS
calls are not the bottleneck this project is optimizing for (KMS API cost
and rate limits were the concern that prompted the decrypt cache; the same
concern does not apply to STS in the same way).
"""

from __future__ import annotations

import json
from typing import Any, cast

import boto3
from botocore.exceptions import ClientError

from outbound_egress.deliver.errors import RetryableDeliveryError
from outbound_egress.model.types import DeliveryResult, KinesisDestination

ASSUME_ROLE_SESSION_NAME = "aqueduct-distributor"


def deliver_kinesis(
    destination: KinesisDestination,
    tenant_id: str,
    customer_id: str,
    payload: dict[str, Any],
    sts_client: Any = None,
    kinesis_client: Any = None,
) -> DeliveryResult:
    """Deliver payload to destination.stream_name in destination.account_id.

    `sts_client` and `kinesis_client` accept injected boto3 clients for
    testing (moto). When `kinesis_client` is not supplied, one is built from
    the assumed-role credentials returned by STS, targeting
    destination.region.
    """
    sts = sts_client or boto3.client("sts")
    role_arn = f"arn:aws:iam::{destination.account_id}:role/{destination.role_name}"

    try:
        assumed = sts.assume_role(RoleArn=role_arn, RoleSessionName=ASSUME_ROLE_SESSION_NAME)
    except ClientError as exc:
        raise RetryableDeliveryError(f"failed to assume role {role_arn}: {exc}") from exc

    credentials = cast("Any", assumed["Credentials"])
    kinesis = kinesis_client or _build_kinesis_client(destination, credentials)

    partition_key = f"{tenant_id}#{customer_id}"
    try:
        response = kinesis.put_record(
            StreamName=destination.stream_name,
            Data=json.dumps(payload).encode("utf-8"),
            PartitionKey=partition_key,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        raise RetryableDeliveryError(
            f"kinesis put_record to {destination.stream_name} failed ({code}): {exc}"
        ) from exc

    return DeliveryResult(success=True, status_code=response["ResponseMetadata"]["HTTPStatusCode"])


def _build_kinesis_client(destination: KinesisDestination, credentials: dict[str, Any]) -> Any:
    return boto3.client(
        "kinesis",
        region_name=destination.region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )
