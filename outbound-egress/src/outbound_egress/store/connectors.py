"""Connector table access: batch read by name, create, update, delete.

Placeholder. Will define:
  - get_connectors(tenant_id, connector_names) -> list[Connector]
    (batch read, used by aqueduct-distributor)
  - list_connectors(tenant_id) -> list[Connector]
    (integration attribute absent - plain webhooks only)
  - list_integrations(tenant_id) -> list[Connector]
    (integration attribute present)
  - create_connector(connector) -> None
  - delete_connector(tenant_id, connector_name) -> None
  - update_connection_status(tenant_id, connector_name, status_code, timestamp)
    -> None
"""
