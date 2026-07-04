"""Lambda entry point: backfill-mci.

Receives a batch of external customer IDs for one tenant and creates their MCI
mappings using the day-0 strategy: internal_customer_id == external_customer_id.
It reaches MCI through mci-core's invoker (the single write path), supplying the
internal id per key so no fresh UUID is minted for historical records.

Input  : {"tenant_id": str, "customer_ids": [str], "day0": true}
Output : {"records_processed": int}

TODO: build CustomerKey list with internal_customer_id == customer_id (when
day0), call mci.invoker.client.resolve_internal_ids, count and return.
"""

from __future__ import annotations


def handler(event: dict, context=None) -> dict:
    raise NotImplementedError
