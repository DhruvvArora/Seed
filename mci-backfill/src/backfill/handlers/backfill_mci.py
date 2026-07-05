"""Lambda entry point: backfill-mci.

Receives a batch of external customer IDs for one tenant and creates their MCI
mappings using the day-0 strategy: internal_customer_id == external_customer_id,
so historical records stay queryable under the same key during the transition.

It does NOT write to DynamoDB itself. It builds the request and invokes the MCI
`get-internal-customer-ids` Lambda through mci-core's invoker, supplying the
internal id per key. MCI performs the actual conditional, 20x-parallel writes.
That single write path is why running the backfill twice is safe: MCI's
conditional create returns the existing mapping on a repeat rather than erroring.

Input  : {"tenant_id": str, "customer_ids": [str], "day0": bool}
Output : {"records_processed": int}
"""

from __future__ import annotations

import os

from mci.invoker.client import resolve_internal_ids
from mci.model.types import CustomerKey

# Which MCI function/alias to invoke. Callers never hard-code the ARN; these come
# from Terraform-wired env vars (alias defaults to the production LIVE alias).
MCI_FUNCTION_NAME = os.environ.get("MCI_FUNCTION_NAME", "")
MCI_FUNCTION_ALIAS = os.environ.get("MCI_FUNCTION_ALIAS", "LIVE")


def _build_keys(tenant_id: str, customer_ids: list[str], day0: bool) -> list[CustomerKey]:
    """Turn raw external ids into CustomerKeys.

    day0 pins internal == external (the migration strategy). day0=False leaves
    the internal id unset so MCI mints a fresh UUID, which is the normal path
    kept here for completeness even though backfill always runs with day0=True.
    """
    return [
        CustomerKey(
            tenant_id=tenant_id,
            customer_id=cid,
            internal_customer_id=cid if day0 else None,
        )
        for cid in customer_ids
    ]


def handler(event: dict, context=None) -> dict[str, int]:
    tenant_id = event["tenant_id"]
    customer_ids = list(event["customer_ids"])
    day0 = bool(event.get("day0", True))  # backfill's whole purpose; default on

    keys = _build_keys(tenant_id, customer_ids, day0)
    resolved = resolve_internal_ids(
        MCI_FUNCTION_NAME,
        keys,
        qualifier=MCI_FUNCTION_ALIAS,
    )

    # MCI deduplicates internally, so the resolved map holds one entry per unique
    # external id for this tenant. That is the honest count of records processed.
    records_processed = len(resolved.get(tenant_id, {}))
    return {"records_processed": records_processed}
