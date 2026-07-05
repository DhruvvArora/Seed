"""Lambda entry point: list-tenants.

Returns the active tenant IDs the Step Function's Map state fans out over, read
from the tenant registry, with known test/internal tenants filtered out.

Output : {"tenants": [str]}

Module-scope config runs once per warm container. Tests monkeypatch these
attributes to point at a moto-backed table.
"""

from __future__ import annotations

import os

from backfill.store.tenant_registry import list_active_tenants

TENANT_TABLE_NAME = os.environ.get("TENANT_TABLE_NAME", "")
# Comma-separated well-known test/internal tenant IDs to exclude, e.g.
# "tenant-test,tenant-internal". Empty by default.
EXCLUDED_TENANT_IDS = [
    t.strip() for t in os.environ.get("EXCLUDED_TENANT_IDS", "").split(",") if t.strip()
]


def handler(event: dict, context=None) -> dict[str, list[str]]:
    tenants = list_active_tenants(TENANT_TABLE_NAME, EXCLUDED_TENANT_IDS)
    return {"tenants": tenants}
