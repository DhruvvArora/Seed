"""Read access to the tenant registry table.

The registry is the source of truth for which tenants the backfill Map state
fans out over. The spec does not pin the table's schema, so this module defines
the minimal shape it needs:

  partition_key : `tenant_id` (String)
  attribute     : `active` (Bool, optional) -- a tenant is treated as active
                  unless it is explicitly set to False, so a registry that omits
                  the flag still works.

Test/internal tenants are excluded by their well-known IDs, passed in by the
caller (the handler reads them from an env var). Keeping the exclusion list in
config rather than hardcoded means adding a test tenant never needs a code
change.
"""

from __future__ import annotations

from collections.abc import Iterable

import boto3


def list_active_tenants(
    table_name: str,
    excluded_ids: Iterable[str],
    dynamodb_resource=None,
) -> list[str]:
    """Return the sorted, deduplicated active tenant IDs, minus excluded ones.

    Scans the whole registry with pagination (the table is small, one row per
    tenant, so a scan is appropriate). A tenant is dropped if it is explicitly
    inactive or if its ID is in `excluded_ids`.
    """
    resource = dynamodb_resource or boto3.resource("dynamodb")
    table = resource.Table(table_name)
    excluded = set(excluded_ids)

    tenants: set[str] = set()
    scan_kwargs: dict = {}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            tenant_id = str(item["tenant_id"])
            if tenant_id in excluded:
                continue
            if item.get("active") is False:  # explicit opt-out only
                continue
            tenants.add(tenant_id)

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    return sorted(tenants)
