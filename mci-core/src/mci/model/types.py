"""Typed request/response/item structures for MCI.

Mirrors the Go `internal/model` package: the customer_keys input, the
nested tenant->external->internal output map, and the DynamoDB item shape
(partition_key, sort_key, tenant_id, external_customer_id,
internal_customer_id, events audit list).

TODO (next step): define dataclasses for these shapes.
"""
