"""aqueduct-writer: CRUD API for connector management.

Invoked by the GraphQL API layer, not directly by clients.

Placeholder for commit 7. Will define handler(event, context) covering:
  - CreateConnector: single connector row, destination KMS-encrypted before
    storage
  - CreateIntegration: fan out into 3 to 6 connector rows (one per offer
    state type), each carrying an integration attribute with integration
    type, environment, and encrypted credentials
  - UpdateConnector, DeleteConnector (removes all rows for an integration)
"""


def handler(event, context):
    raise NotImplementedError
