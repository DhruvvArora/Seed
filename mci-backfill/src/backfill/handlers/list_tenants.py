"""Lambda entry point: list-tenants.

Returns the active tenant IDs the Map state fans out over, read from the tenant
registry DynamoDB table, with known test/internal tenants filtered out.

Output : {"tenants": [str]}

TODO: scan/query the tenant registry, drop well-known test tenant ids, return.
"""

from __future__ import annotations


def handler(event: dict, context=None) -> dict:
    raise NotImplementedError
