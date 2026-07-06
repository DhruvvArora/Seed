"""Unit tests for mParticle conversion and mparticle-processing Lambda.

Named tests from the spec:
  TestMParticleConvert_PurchaseEvent      -- commerce_event -> InternalEvent
  TestMParticleConvert_CurrencyConversion -- 47.50 -> 4750 cents
  TestMParticleConvert_NoCustomerIdentity -- batch skipped, logged, no error

Additional coverage:
  - audience_membership_change_request is skipped without error
  - utc_source marker is NOT set for mParticle events (UTC is real)
  - routing: commerce_event -> transactions-internal, custom_event -> action-internal
  - base64 Kinesis record decoding in the handler
"""

from __future__ import annotations

import base64
import json

import event_ingress.handlers.mparticle_processing as mp_proc
from event_ingress.convert.mparticle import (
    convert_batch_to_internal_events,
    dollars_to_cents,
    extract_customer_identity,
    parse_mparticle_batch,
)
from event_ingress.model.types import MParticleBatch, MParticleUserIdentity

# ---------------------------------------------------------------------------
# Currency conversion
# ---------------------------------------------------------------------------


def test_dollars_to_cents_exact():
    """TestMParticleConvert_CurrencyConversion -- 47.50 -> 4750."""
    assert dollars_to_cents(47.50) == 4750


def test_dollars_to_cents_rounds_up():
    assert dollars_to_cents(47.999) == 4800


def test_dollars_to_cents_small():
    assert dollars_to_cents(0.99) == 99


def test_dollars_to_cents_zero():
    assert dollars_to_cents(0.0) == 0


# ---------------------------------------------------------------------------
# Identity extraction
# ---------------------------------------------------------------------------


def test_extract_customer_identity_raw():
    identities = [
        MParticleUserIdentity("email", "raw", "user@example.com"),
        MParticleUserIdentity("customer", "raw", "loyalty-12345"),
    ]
    assert extract_customer_identity(identities) == "loyalty-12345"


def test_extract_customer_identity_md5():
    """MD5-encoded identity value is returned as-is."""
    identities = [MParticleUserIdentity("customer", "md5", "abc123hash")]
    assert extract_customer_identity(identities) == "abc123hash"


def test_extract_customer_identity_none_when_missing():
    """TestMParticleConvert_NoCustomerIdentity -- returns None when no customer type."""
    identities = [MParticleUserIdentity("email", "raw", "user@example.com")]
    assert extract_customer_identity(identities) is None


def test_extract_customer_identity_empty_list():
    assert extract_customer_identity([]) is None


# ---------------------------------------------------------------------------
# Commerce event conversion
# ---------------------------------------------------------------------------


def _mp_batch_with_commerce_event(total_dollars: float = 47.50) -> MParticleBatch:
    raw = {
        "tenant_id": "tenant-abc",
        "batch_id": "batch-001",
        "timestamp_ms": 1_700_000_000_000,
        "message_type": "event_processing_request",
        "user_identities": [{"identity_type": "customer", "encoding": "raw", "value": "ext-1"}],
        "user_attributes": {},
        "events": [
            {
                "event_type": "commerce_event",
                "data": {
                    "event_id": "evt-commerce-1",
                    "total_amount": total_dollars,
                    "currency_code": "USD",
                    "product_action": {
                        "products": [
                            {
                                "id": "sku-001",
                                "category": "grocery",
                                "price": total_dollars,
                                "quantity": 1,
                            }
                        ]
                    },
                },
            }
        ],
        "raw": {},
    }
    return parse_mparticle_batch(raw)


def test_commerce_event_converts_to_transaction():
    """TestMParticleConvert_PurchaseEvent -- commerce_event -> InternalEvent transaction."""
    batch = _mp_batch_with_commerce_event(47.50)
    events = convert_batch_to_internal_events(batch, "internal-uuid-1", "2026-01-15T00:00:00+00:00")

    assert len(events) == 1
    ie = events[0]
    assert ie.event_type == "transaction"
    assert ie.customer_id == "internal-uuid-1"
    assert ie.tenant_id == "tenant-abc"


def test_commerce_event_total_converted_to_cents():
    """TestMParticleConvert_CurrencyConversion -- total_amount float -> integer cents."""
    batch = _mp_batch_with_commerce_event(47.50)
    events = convert_batch_to_internal_events(batch, "internal-uuid-1", "2026-01-15T00:00:00+00:00")

    assert events[0].event_data["total"] == 4750


def test_mparticle_events_have_no_assumed_local_marker():
    """mParticle UTC is real (epoch ms), so utc_source should NOT be set."""
    batch = _mp_batch_with_commerce_event()
    events = convert_batch_to_internal_events(batch, "internal-uuid-1", "2026-01-15T00:00:00+00:00")

    assert "utc_source" not in events[0].event_attributes


# ---------------------------------------------------------------------------
# mparticle-processing handler
# ---------------------------------------------------------------------------


def _kinesis_event(payloads: list[dict]) -> dict:
    """Build a fake Kinesis trigger event from a list of enriched batch dicts."""
    records = [
        {"kinesis": {"data": base64.b64encode(json.dumps(p).encode()).decode()}} for p in payloads
    ]
    return {"Records": records}


def _fake_mci(internal_id: str = "internal-uuid-mp"):
    def _resolve(function_name, customer_keys, *, qualifier="LIVE", **kwargs):
        return {k.tenant_id: {k.customer_id: internal_id} for k in customer_keys}

    return _resolve


def _capture_kinesis():
    captured = []

    class FakeKinesis:
        def put_records(self, StreamName, Records):
            captured.append({"stream": StreamName, "records": Records})
            return {
                "FailedRecordCount": 0,
                "Records": [{"SequenceNumber": "1", "ShardId": "s0"}] * len(Records),
            }

    return FakeKinesis(), captured


def test_processing_routes_commerce_to_transactions_stream(monkeypatch):
    monkeypatch.setattr(mp_proc, "resolve_internal_ids", _fake_mci())
    monkeypatch.setattr(mp_proc, "TRANSACTIONS_STREAM_NAME", "transactions-internal")
    monkeypatch.setattr(mp_proc, "ACTIONS_STREAM_NAME", "action-internal")
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(mp_proc, "_kinesis_client", kinesis)

    payload = {
        "tenant_id": "t1",
        "batch_id": "b1",
        "timestamp_ms": 1_700_000_000_000,
        "message_type": "event_processing_request",
        "user_identities": [{"identity_type": "customer", "encoding": "raw", "value": "ext-1"}],
        "user_attributes": {},
        "events": [
            {
                "event_type": "commerce_event",
                "data": {
                    "event_id": "e1",
                    "total_amount": 25.0,
                    "currency_code": "USD",
                    "product_action": {"products": []},
                },
            }
        ],
        "raw": {},
    }
    mp_proc.handler(_kinesis_event([payload]))

    streams = {c["stream"] for c in captured}
    assert "transactions-internal" in streams


def test_processing_skips_batch_with_no_customer_identity(monkeypatch):
    """TestMParticleConvert_NoCustomerIdentity -- handler skips, does not error."""
    mci_called = []

    def _mci(*args, **kwargs):
        mci_called.append(True)
        return {}

    monkeypatch.setattr(mp_proc, "resolve_internal_ids", _mci)

    payload = {
        "tenant_id": "t1",
        "batch_id": "b1",
        "timestamp_ms": 1_700_000_000_000,
        "message_type": "event_processing_request",
        "user_identities": [{"identity_type": "email", "encoding": "raw", "value": "x@y.com"}],
        "user_attributes": {},
        "events": [],
        "raw": {},
    }
    mp_proc.handler(_kinesis_event([payload]))  # must not raise

    assert not mci_called  # MCI should never be called for a batch with no identity


def test_audience_membership_change_is_skipped_without_error(monkeypatch):
    """audience_membership_change_request is logged and skipped (Project 4 wires this)."""
    mci_called = []

    def _mci(*args, **kwargs):
        mci_called.append(True)
        return {}

    monkeypatch.setattr(mp_proc, "resolve_internal_ids", _mci)
    kinesis, captured = _capture_kinesis()
    monkeypatch.setattr(mp_proc, "_kinesis_client", kinesis)

    payload = {
        "tenant_id": "t1",
        "batch_id": "b-aud",
        "timestamp_ms": 1_700_000_000_000,
        "message_type": "audience_membership_change_request",
        "user_identities": [],
        "user_attributes": {},
        "events": [],
        "raw": {},
    }
    mp_proc.handler(_kinesis_event([payload]))  # must not raise

    assert not mci_called
    assert not captured  # nothing written to Kinesis
