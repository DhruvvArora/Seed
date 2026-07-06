"""Unit tests for the event-enqueue Lambda.

Named tests from the spec:
  TestEventEnqueue_Transaction      -- valid transaction event lands in transactions-internal
  TestEventEnqueue_Action           -- valid action event lands in action-internal
  TestEventEnqueue_MissingEventID   -- returns 400
  TestEventEnqueue_MCIResolution    -- customer_id is replaced with internal UUID

Additional coverage:
  - missing customer_id returns 400
  - missing event_time returns 400
  - invalid event_type returns 400
  - negative transaction.total returns 400
  - utc_source marker is present on every event
  - MCI failure returns 500
"""

from __future__ import annotations

import json

import event_ingress.handlers.event_enqueue as ee

TENANT = "tenant-abc"
METHOD_ARN = "arn:aws:execute-api:us-east-1:123:api/dev/POST/event"


def _api_gw_event(body: dict, tenant_id: str = TENANT) -> dict:
    """Minimal API Gateway Lambda proxy event."""
    return {
        "body": json.dumps(body),
        "requestContext": {"authorizer": {"tenant_id": tenant_id}},
    }


def _tx_body(
    event_id: str = "ev-001",
    customer_id: str = "ext-cust-1",
    total: int = 4750,
) -> dict:
    return {
        "event_id": event_id,
        "customer_id": customer_id,
        "event_time": "2026-01-15T14:30:00",
        "event_type": "transaction",
        "transaction": {
            "total": total,
            "currency": "USD",
            "items": {"item-1": {"category": "groceries", "price": 4750, "qty": 1}},
        },
    }


def _action_body(
    event_id: str = "ev-002",
    customer_id: str = "ext-cust-1",
) -> dict:
    return {
        "event_id": event_id,
        "customer_id": customer_id,
        "event_time": "2026-01-15T14:31:00",
        "event_type": "action",
        "action": {"action_id": "product_scan", "attributes": {"sku": "ABC123"}},
    }


def _fake_mci(internal_id: str = "internal-uuid-999"):
    """Returns a fake resolve_internal_ids that always maps to a fixed internal ID."""

    def _resolve(function_name, customer_keys, *, qualifier="LIVE", **kwargs):
        return {k.tenant_id: {k.customer_id: internal_id} for k in customer_keys}

    return _resolve


def _capture_kinesis():
    """Returns a fake Kinesis client that captures put_records calls."""
    captured = []

    class FakeKinesis:
        def put_records(self, StreamName, Records):
            captured.append({"stream": StreamName, "records": Records})
            n = len(Records)
            stub = {"SequenceNumber": "1", "ShardId": "s0"}
            return {"FailedRecordCount": 0, "Records": [stub] * n}

    return FakeKinesis(), captured


# ---------------------------------------------------------------------------
# Tests: happy paths
# ---------------------------------------------------------------------------


def test_transaction_event_routes_to_transactions_stream(monkeypatch):
    """TestEventEnqueue_Transaction -- transaction lands in transactions-internal."""
    monkeypatch.setattr(ee, "resolve_internal_ids", _fake_mci("internal-uuid-tx"))
    monkeypatch.setattr(ee, "TRANSACTIONS_STREAM_NAME", "transactions-internal")
    monkeypatch.setattr(ee, "ACTIONS_STREAM_NAME", "action-internal")
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(ee, "_kinesis_client", kinesis)

    result = ee.handler(_api_gw_event(_tx_body()))

    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    assert body["status"] == "ok"
    assert body["event_id"] == "ev-001"
    assert len(captured) == 1
    assert captured[0]["stream"] == "transactions-internal"


def test_action_event_routes_to_action_stream(monkeypatch):
    """TestEventEnqueue_Action -- action lands in action-internal."""
    monkeypatch.setattr(ee, "resolve_internal_ids", _fake_mci("internal-uuid-act"))
    monkeypatch.setattr(ee, "TRANSACTIONS_STREAM_NAME", "transactions-internal")
    monkeypatch.setattr(ee, "ACTIONS_STREAM_NAME", "action-internal")
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(ee, "_kinesis_client", kinesis)

    result = ee.handler(_api_gw_event(_action_body()))

    assert result["statusCode"] == 200
    assert len(captured) == 1
    assert captured[0]["stream"] == "action-internal"


def test_mci_resolution_replaces_customer_id(monkeypatch):
    """TestEventEnqueue_MCIResolution -- the event in Kinesis uses the internal UUID."""
    internal_id = "resolved-internal-uuid"
    monkeypatch.setattr(ee, "resolve_internal_ids", _fake_mci(internal_id))
    monkeypatch.setattr(ee, "TRANSACTIONS_STREAM_NAME", "transactions-internal")
    monkeypatch.setattr(ee, "ACTIONS_STREAM_NAME", "action-internal")
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(ee, "_kinesis_client", kinesis)

    ee.handler(_api_gw_event(_tx_body(customer_id="ext-original")))

    record_data = json.loads(captured[0]["records"][0]["Data"])
    assert record_data["customer_id"] == internal_id
    assert record_data["customer_id"] != "ext-original"


def test_utc_source_marker_present(monkeypatch):
    """event_attributes["utc_source"] == "assumed_local" on every REST API event."""
    monkeypatch.setattr(ee, "resolve_internal_ids", _fake_mci())
    monkeypatch.setattr(ee, "TRANSACTIONS_STREAM_NAME", "transactions-internal")
    monkeypatch.setattr(ee, "ACTIONS_STREAM_NAME", "action-internal")
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(ee, "_kinesis_client", kinesis)

    ee.handler(_api_gw_event(_tx_body()))

    record_data = json.loads(captured[0]["records"][0]["Data"])
    assert record_data["event_attributes"]["utc_source"] == "assumed_local"


def test_partition_key_contains_internal_id(monkeypatch):
    """Kinesis partition key is tenant_id#internal_customer_id."""
    internal_id = "internal-123"
    monkeypatch.setattr(ee, "resolve_internal_ids", _fake_mci(internal_id))
    monkeypatch.setattr(ee, "TRANSACTIONS_STREAM_NAME", "transactions-internal")
    monkeypatch.setattr(ee, "ACTIONS_STREAM_NAME", "action-internal")
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(ee, "_kinesis_client", kinesis)

    ee.handler(_api_gw_event(_tx_body()))

    pk = captured[0]["records"][0]["PartitionKey"]
    assert pk == f"{TENANT}#{internal_id}"


# ---------------------------------------------------------------------------
# Tests: validation failures (400)
# ---------------------------------------------------------------------------


def test_missing_event_id_returns_400(monkeypatch):
    """TestEventEnqueue_MissingEventID -- returns 400."""
    body = _tx_body()
    del body["event_id"]
    result = ee.handler(_api_gw_event(body))
    assert result["statusCode"] == 400
    assert "event_id" in json.loads(result["body"])["message"]


def test_missing_customer_id_returns_400(monkeypatch):
    body = _tx_body()
    del body["customer_id"]
    result = ee.handler(_api_gw_event(body))
    assert result["statusCode"] == 400


def test_missing_event_time_returns_400(monkeypatch):
    body = _tx_body()
    del body["event_time"]
    result = ee.handler(_api_gw_event(body))
    assert result["statusCode"] == 400


def test_invalid_event_type_returns_400(monkeypatch):
    body = _tx_body()
    body["event_type"] = "purchase"
    result = ee.handler(_api_gw_event(body))
    assert result["statusCode"] == 400


def test_negative_transaction_total_returns_400(monkeypatch):
    body = _tx_body(total=-100)
    result = ee.handler(_api_gw_event(body))
    assert result["statusCode"] == 400


def test_missing_tenant_id_returns_401(monkeypatch):
    """No authorizer context means the request was not authenticated."""
    event = {"body": json.dumps(_tx_body()), "requestContext": {"authorizer": {}}}
    result = ee.handler(event)
    assert result["statusCode"] == 401


# ---------------------------------------------------------------------------
# Tests: downstream failures (500)
# ---------------------------------------------------------------------------


def test_mci_failure_returns_500(monkeypatch):
    from mci.invoker.client import MciInvokeError

    def _fail(*args, **kwargs):
        raise MciInvokeError("DynamoDB unavailable")

    monkeypatch.setattr(ee, "resolve_internal_ids", _fail)
    result = ee.handler(_api_gw_event(_tx_body()))
    assert result["statusCode"] == 500
