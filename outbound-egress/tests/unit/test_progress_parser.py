"""Tests for outbound_egress.progress.parser."""

import base64
import json

from outbound_egress.progress.parser import decode_kinesis_record, parse_batch_progress


def _sample_batch_dict():
    return {
        "trace": {"trace_id": "abc-123"},
        "edge_time": "2026-07-06T12:00:00+00:00",
        "progresses": [
            {
                "tenant_id": "tenant-abc",
                "internal_customer_id": "uuid-1",
                "offer_id": "offer-1",
                "campaign_id": "campaign-1",
                "connectors": ["RetailBrandWebhook"],
                "status": "ACHIEVED",
            }
        ],
    }


def test_parse_batch_progress_from_decoded_dict():
    batch = parse_batch_progress(_sample_batch_dict())
    assert batch.progresses[0].tenant_id == "tenant-abc"
    assert batch.progresses[0].connectors == ["RetailBrandWebhook"]


def test_decode_kinesis_record_base64_decodes_and_parses():
    raw_json = json.dumps(_sample_batch_dict()).encode("utf-8")
    record = {"kinesis": {"data": base64.b64encode(raw_json).decode("ascii")}}

    batch = decode_kinesis_record(record)

    assert batch.trace["trace_id"] == "abc-123"
    assert len(batch.progresses) == 1
    assert batch.progresses[0].offer_id == "offer-1"
