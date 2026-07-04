"""Unit tests for the MCI invoker client.

These use a hand-rolled fake Lambda client rather than moto, because we only
need to verify the marshaling contract (what payload we send) and the
unmarshaling contract (how we read the response), not real Lambda execution.
The fake records the last invoke call so we can assert on it.
"""

from __future__ import annotations

import io
import json

import pytest

from mci.invoker.client import MciInvokeError, resolve_internal_ids
from mci.model.types import CustomerKey


class _FakeLambdaClient:
    def __init__(self, response_body, function_error=None):
        self._response_body = response_body
        self._function_error = function_error
        self.last_call: dict | None = None

    def invoke(self, **kwargs):
        self.last_call = kwargs
        resp: dict = {"Payload": io.BytesIO(json.dumps(self._response_body).encode("utf-8"))}
        if self._function_error:
            resp["FunctionError"] = self._function_error
        return resp

    def sent_payload(self) -> dict:
        assert self.last_call is not None
        return json.loads(self.last_call["Payload"])


def test_resolve_marshals_and_unmarshals():
    client = _FakeLambdaClient({"t1": {"ext1": "uuid-x"}})

    result = resolve_internal_ids(
        "mci-get-internal",
        [CustomerKey("t1", "ext1")],
        qualifier="LIVE",
        lambda_client=client,
    )

    assert result == {"t1": {"ext1": "uuid-x"}}
    assert client.last_call["FunctionName"] == "mci-get-internal"
    assert client.last_call["Qualifier"] == "LIVE"
    assert client.last_call["InvocationType"] == "RequestResponse"

    sent = client.sent_payload()
    assert sent["customer_keys"] == [{"tenant_id": "t1", "customer_id": "ext1"}]
    assert "internal_customer_id" not in sent["customer_keys"][0]  # unpinned
    assert "read_only" not in sent  # omitted when false


def test_resolve_includes_supplied_internal_id():
    """A pinned internal id (backfill day-0: internal==external) is sent through."""
    client = _FakeLambdaClient({"t1": {"ext1": "ext1"}})

    resolve_internal_ids(
        "mci-get-internal",
        [CustomerKey("t1", "ext1", internal_customer_id="ext1")],
        lambda_client=client,
    )

    sent = client.sent_payload()
    assert sent["customer_keys"][0]["internal_customer_id"] == "ext1"


def test_resolve_read_only_flag():
    client = _FakeLambdaClient({"t1": {"ext1": "uuid-x"}})

    resolve_internal_ids(
        "mci-get-internal",
        [CustomerKey("t1", "ext1")],
        read_only=True,
        lambda_client=client,
    )

    assert client.sent_payload()["read_only"] is True


def test_resolve_raises_on_function_error():
    client = _FakeLambdaClient(
        {"errorMessage": "boom", "errorType": "KeyError"},
        function_error="Unhandled",
    )

    with pytest.raises(MciInvokeError, match="boom"):
        resolve_internal_ids(
            "mci-get-internal",
            [CustomerKey("t1", "ext1")],
            lambda_client=client,
        )
