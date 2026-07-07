"""Connector, Destination, BatchProgress, Progress, and Transformation types.

Placeholder for commit 2 (Models). Will define:
  - Connector: tenant_id, name, payload_type, destination_type, destination
    (decrypted at runtime), transformation_name, integration, connection_status,
    enabled
  - Destination: tagged union of WebhookDestination, KinesisDestination,
    JwtOAuthDestination
  - BatchProgress: trace block, edge_time, progresses (list[Progress])
  - Progress: tenant_id, internal_customer_id, offer_id, campaign_id,
    campaign window timestamps, connectors (list[str]), metadata, all_outcomes,
    status
  - Transformation: tenant_id, name, jq_script
"""
