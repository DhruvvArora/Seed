"""Client library other services import to call MCI via Lambda Invoke.

This is the Python equivalent of the Go `api/invoker` package, reused by
Projects 2, 3, and 4. Callers never hard-code the MCI ARN; they pass the
function name and an alias qualifier (LIVE / CANARY) from their own env vars,
so the wiring stays in Terraform/config, not in code.

Why invoke the Lambda instead of importing the store directly? So there is a
single write path into the MCI table. Every mapping, whoever created it, flows
through the same conditional-write race handling and the same S3 audit log.
Duplicating the write logic in each caller is exactly the drift the platform
avoids.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from mci.model.types import CustomerKey

# The production alias. Callers can override per-invocation (e.g. CANARY).
DEFAULT_QUALIFIER = "LIVE"


class MciInvokeError(RuntimeError):
    """Raised when the MCI Lambda itself returns an error (FunctionError set).

    This is distinct from a transport error (which boto3 raises as a
    ClientError): it means the invoke reached MCI but the handler raised.
    """


def _serialize_key(key: CustomerKey) -> dict[str, str]:
    entry = {"tenant_id": key.tenant_id, "customer_id": key.customer_id}
    # Only include the internal id when the caller pinned one. Omitting it is
    # the normal path where MCI mints a fresh UUID.
    if key.internal_customer_id is not None:
        entry["internal_customer_id"] = key.internal_customer_id
    return entry


def resolve_internal_ids(
    function_name: str,
    customer_keys: list[CustomerKey],
    *,
    qualifier: str = DEFAULT_QUALIFIER,
    read_only: bool = False,
    lambda_client: Any = None,
) -> dict[str, dict[str, str]]:
    """Resolve external customer IDs to internal UUIDs via the MCI Lambda.

    Marshals `customer_keys` into the get-internal-customer-ids request shape,
    invokes the function synchronously at the given alias, and unmarshals the
    nested `{tenant_id: {external_customer_id: internal_customer_id}}` map.

    A caller that pins `internal_customer_id` on a key (as backfill does, with
    internal == external) gets that id honored for any newly created mapping.

    Raises:
        MciInvokeError: the invoke succeeded but the MCI handler raised.
    """
    client = lambda_client or boto3.client("lambda")

    payload: dict[str, Any] = {
        "customer_keys": [_serialize_key(k) for k in customer_keys],
    }
    if read_only:
        payload["read_only"] = True

    response = client.invoke(
        FunctionName=function_name,
        Qualifier=qualifier,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )

    raw = response["Payload"].read()
    parsed: Any = json.loads(raw) if raw else None

    # When the handler raises, Lambda still returns 200 at the transport layer
    # but sets FunctionError and puts the error detail in the payload. Surface
    # it as our own error rather than letting a stack-trace dict masquerade as
    # a result map.
    if response.get("FunctionError"):
        message = ""
        if isinstance(parsed, dict):
            message = parsed.get("errorMessage", "")
        raise MciInvokeError(f"MCI invoke failed ({response['FunctionError']}): {message}")

    if not isinstance(parsed, dict):
        raise MciInvokeError(f"MCI returned an unexpected payload: {parsed!r}")

    return parsed
