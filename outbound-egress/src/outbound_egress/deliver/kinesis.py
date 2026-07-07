"""Cross-account Kinesis delivery.

Placeholder for commit 5 (Delivery layer). Will define:
  - deliver_kinesis(destination, tenant_id, customer_id, payload)
    -> DeliveryResult
  - Assume the configured IAM role via STS in the destination account/region
  - PutRecord with partition key f"{tenant_id}#{customer_id}", matching the
    internal streams' partitioning scheme
  - Retry up to 3 times on throughput exceeded errors
"""
