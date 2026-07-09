"""aqueduct-reader: internal connector config loader.

Invoked via Lambda Invoke by other internal AWS services that need to look
up connector configs without going through the GraphQL API layer (that is
what aqueduct-writer serves). Read-only and batch-oriented.

Deliberately returns the same encrypted-destination-omitting summary view
as aqueduct-writer (Connector.to_summary_dict), and never decrypts.
Decrypting a destination is a delivery-time concern owned exclusively by
aqueduct-distributor's invocation-scoped DecryptCache. Handing decrypted
secrets back through a general-purpose internal reader widens the blast
radius of anything in the account that can invoke this Lambda, so it stays
out of scope here on purpose.

Input shape: {"tenant_id": ..., "connector_names": [...]}
Output shape: {"ConnectorName": summary_dict, "MissingConnectorName": None, ...}
one entry per requested name, in the same order requested is not
guaranteed since this returns a dict, but every requested name is a key.
"""

from __future__ import annotations

import os
from typing import Any

from outbound_egress.store.connectors import ConnectorStore

CONNECTORS_TABLE_NAME = os.environ.get("CONNECTORS_TABLE_NAME", "")

_connector_store: ConnectorStore | None = None


def _get_connector_store() -> ConnectorStore:
    global _connector_store
    if _connector_store is None:
        _connector_store = ConnectorStore(CONNECTORS_TABLE_NAME)
    return _connector_store


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    tenant_id = event["tenant_id"]
    connector_names: list[str] = event.get("connector_names", [])

    found = _get_connector_store().get_connectors(tenant_id, connector_names)

    return {
        name: (found[name].to_summary_dict() if name in found else None) for name in connector_names
    }
