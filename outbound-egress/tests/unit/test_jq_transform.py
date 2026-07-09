"""Tests for outbound_egress.transform.jq_transform.

pytest.importorskip("jq") guards every test that needs the real jq package,
even though it's declared in dev deps here, since a contributor's machine
might not have it installed (e.g. a fresh checkout before `pip install -e
".[dev]"`), matching the defensive pattern used for optional/layer-only deps
elsewhere in the monorepo (see audience-ingress's pandas-guarded parquet test).
"""

import pytest

from outbound_egress.transform.jq_transform import TransformationError, apply_transformation

jq = pytest.importorskip("jq")


def test_no_transformation_configured_returns_payload_unchanged():
    payload = {"offer_id": "offer-1", "status": "ACHIEVED"}
    assert apply_transformation(None, payload) == payload
    assert apply_transformation("", payload) == payload


def test_jq_transformation_produces_valid_mparticle_style_payload():
    jq_script = (
        '{event_type: "commerce_event", customer_id: .internal_customer_id, '
        "data: {offer_id: .offer_id, status: .status}}"
    )
    payload = {
        "internal_customer_id": "uuid-1234",
        "offer_id": "offer-1",
        "status": "ACHIEVED",
    }

    result = apply_transformation(jq_script, payload)

    assert result == {
        "event_type": "commerce_event",
        "customer_id": "uuid-1234",
        "data": {"offer_id": "offer-1", "status": "ACHIEVED"},
    }


def test_malformed_jq_script_returns_error_and_does_not_panic():
    with pytest.raises(TransformationError, match="jq transformation failed"):
        apply_transformation("this is not valid jq {{{", {"a": 1})


def test_jq_runtime_error_returns_transformation_error_not_raw_exception():
    # .foo + 1 fails at runtime when foo is a string, not a number.
    with pytest.raises(TransformationError, match="jq transformation failed"):
        apply_transformation(".foo + 1", {"foo": "not a number"})


def test_jq_script_producing_no_output_returns_transformation_error():
    with pytest.raises(TransformationError, match="jq transformation failed"):
        apply_transformation("empty", {"foo": 1})


def test_jq_script_producing_non_object_returns_transformation_error():
    with pytest.raises(TransformationError, match="must produce a JSON object"):
        apply_transformation(".foo", {"foo": "just a string"})
