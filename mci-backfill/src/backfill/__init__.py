"""MCI Backfill Pipeline (Project 2).

One-time migration that assigns internal IDs to every historical
(tenant_id, external_customer_id) pair predating MCI. Orchestrated by an AWS
Step Function; this package holds the Lambda handlers it invokes.
"""
