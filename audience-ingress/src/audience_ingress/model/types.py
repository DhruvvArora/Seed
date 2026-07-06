"""Typed structures for the Audience Ingress Service.

Two DynamoDB tables are modelled here:

  1. bullseye-audience-metadata-v2 -- one row per (tenant, audience) holding
     audience-level metadata: state, size, run_date, parquet_path. The batch
     pipeline's final step writes this. `size` starts at -1 (sentinel meaning
     "created but not yet loaded") and is set to the real member count in a
     single atomic write when a load completes.

  2. audience-membership -- one row per (tenant, audience, internal_customer_id)
     holding that member's state (ELIGIBLE / INELIGIBLE). The streaming reducer
     upserts these. Removals are soft: state flips to INELIGIBLE, the row is
     never hard-deleted.

The mParticle streaming path also carries AudienceChange records off the
audience-events Kinesis stream; those are modelled here too so the reducer
never passes raw dicts around.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# Membership states. Soft-delete means we only ever move between these two;
# a removed member becomes INELIGIBLE, not absent.
STATE_ELIGIBLE = "ELIGIBLE"
STATE_INELIGIBLE = "INELIGIBLE"

# metadata sort key is always this literal (one metadata row per partition).
METADATA_SORT_KEY = "METADATA"

# size sentinel: audience exists but membership has not been loaded yet.
# Downstream offer-build treats this as "not ready" -- an unambiguous signal,
# unlike a partial count which would look like a real (but wrong) size.
SIZE_NOT_LOADED = -1


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp, e.g. '2026-07-05T18:30:00+00:00'."""
    return datetime.now(UTC).isoformat()


def build_partition_key(tenant_id: str, audience_id: str) -> str:
    """Hash key shared by both tables: {tenant_id}#{audience_id}."""
    return f"{tenant_id}#{audience_id}"


@dataclass(frozen=True)
class AudienceChange:
    """One add/remove instruction for a single audience.

    `action` is the raw mParticle verb, either "add" or "delete". The reducer
    maps "add" -> ELIGIBLE and "delete" -> INELIGIBLE.
    """

    audience_id: str
    action: str  # "add" | "delete"

    @property
    def target_state(self) -> str:
        return STATE_ELIGIBLE if self.action == "add" else STATE_INELIGIBLE


@dataclass(frozen=True)
class AudienceEvent:
    """One record off the audience-events Kinesis stream.

    Produced (eventually) by P3's mparticle-processing when it sees an
    audience_membership_change_request. Carries the EXTERNAL customer id; the
    reducer resolves it to an internal UUID via MCI before writing.
    """

    tenant_id: str
    external_customer_id: str
    audience_changes: list[AudienceChange]
    timestamp: str

    @staticmethod
    def from_record(raw: dict) -> AudienceEvent:
        changes = [
            AudienceChange(audience_id=c["audience_id"], action=c["action"])
            for c in raw.get("audience_changes", [])
        ]
        return AudienceEvent(
            tenant_id=raw["tenant_id"],
            external_customer_id=raw["external_customer_id"],
            audience_changes=changes,
            timestamp=raw.get("timestamp", ""),
        )


@dataclass(frozen=True)
class MembershipRow:
    """A row in the audience-membership table.

    `ttl` is optional: streaming events carry no audience end date, so we leave
    it unset (None) rather than invent a value. When an audience end date
    becomes available, callers can pass a Unix timestamp and DynamoDB TTL will
    auto-expire the row.
    """

    tenant_id: str
    audience_id: str
    internal_customer_id: str
    state: str
    updated_at: str
    ttl: int | None = None

    def to_item(self) -> dict:
        item: dict = {
            "partition_key": build_partition_key(self.tenant_id, self.audience_id),
            "sort_key": self.internal_customer_id,
            "tenant_id": self.tenant_id,
            "audience_id": self.audience_id,
            "internal_customer_id": self.internal_customer_id,
            "state": self.state,
            "updated_at": self.updated_at,
        }
        if self.ttl is not None:
            item["ttl"] = self.ttl
        return item


@dataclass(frozen=True)
class ParquetMemberRow:
    """One row written to the audience-membership parquet file.

    Mirrors the parquet schema in the spec exactly (all five columns). The
    three partition-valued columns (tenant_id, audience_id, run_date) are
    redundant with the Hive path but included to match the documented schema;
    no Glue table is defined in P4, so the redundancy is harmless.
    """

    tenant_id: str
    audience_id: str
    internal_customer_id: str
    run_date: str  # YYYY-MM-DD
    state: str
