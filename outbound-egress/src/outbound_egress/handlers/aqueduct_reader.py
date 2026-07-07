"""aqueduct-reader: internal connector config loader.

Placeholder for commit 8. Will define handler(event, context) covering:
  - ListConnectors: rows where the integration attribute is absent
    (plain webhooks only)
  - ListIntegrations: rows where the integration attribute is present
  - DeleteConnector: removes a single connector row by tenant ID and name
"""


def handler(event, context):
    raise NotImplementedError
