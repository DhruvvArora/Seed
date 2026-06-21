"""Lambda entry point: get-internal-customer-ids.

Resolves a batch of (tenant_id, external_customer_id) pairs to stable
internal UUIDs, creating mappings on miss. Module-scope client/cache setup
goes here (runs once per warm container); the handler() function is the
per-invocation entry point.

TODO (next step): implement handler(event, context).
"""


def handler(event, context):
    raise NotImplementedError
