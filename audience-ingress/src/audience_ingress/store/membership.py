"""Membership row store: audience-membership table.

One row per (tenant, audience, internal_customer_id). The streaming reducer
upserts these as add/remove events arrive. Writes are unconditional PutItem:
an add overwrites with ELIGIBLE, a remove overwrites with INELIGIBLE. Last
write wins, which is the correct semantics for a stream of state changes where
the most recent event is the truth.

Soft delete: a "delete" never issues DeleteItem. It writes state=INELIGIBLE so
the row (and its audit of updated_at) survives. Downstream size counts filter
on state=ELIGIBLE.
"""

from __future__ import annotations

from typing import Any

from audience_ingress.model.types import MembershipRow


class MembershipStore:
    """Thin wrapper over the audience-membership DynamoDB table.

    Holds a boto3 table resource. One instance is created per Lambda container
    and reused across invocations (module-level in the handler), so the boto3
    connection pool warms up once.
    """

    def __init__(self, table: Any) -> None:
        self._table = table

    def upsert(self, row: MembershipRow) -> None:
        """Write (or overwrite) a single membership row.

        Unconditional: the row's `state` reflects the latest add/remove. No
        condition expression, because for a stream of changes the newest event
        should win even if an older event for the same member arrives late and
        is retried.
        """
        self._table.put_item(Item=row.to_item())
