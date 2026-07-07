"""Parse BatchProgress messages off the plague Kinesis stream.

Kinesis hands records to a Lambda trigger as base64-encoded bytes under
record["kinesis"]["data"]; decode_kinesis_record does that decoding and the
JSON parse in one step, matching the pattern used in event_ingress's
mparticle_processing handler.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from outbound_egress.model.types import BatchProgress


def parse_batch_progress(raw: dict[str, Any]) -> BatchProgress:
    """Parse an already-decoded JSON dict into a BatchProgress."""
    return BatchProgress.from_dict(raw)


def decode_kinesis_record(record: dict[str, Any]) -> BatchProgress:
    """Decode one raw Kinesis trigger record into a BatchProgress.

    Each progress entry's connectors list tells the distributor exactly
    which connector rows to load and invoke for that event.
    """
    raw_bytes = base64.b64decode(record["kinesis"]["data"])
    raw_json: dict[str, Any] = json.loads(raw_bytes)
    return parse_batch_progress(raw_json)
