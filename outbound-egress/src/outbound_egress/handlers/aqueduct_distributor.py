"""aqueduct-distributor: Kinesis consumer for the plague stream.

Placeholder for commit 6. Will define handler(event, context) that, per
BatchProgress message:
  1. Parses the batch, iterates the progresses array
  2. For each progress entry, reads the connectors list to find which
     connector rows apply
  3. Batch-loads those connector configs from DynamoDB
  4. For each connector: decrypts destination via KMS (using a decrypt
     cache created fresh in this function, keyed by
     f"{tenant_id}#{connector_name}", never at module scope - see
     crypto/kms.py docstring for why), applies the JQ transform if
     configured, delivers, then writes connection_status back to DynamoDB

Concurrency:
  - All connectors for a single progress event: ThreadPoolExecutor
  - Progress events within a batch: processed sequentially, to preserve
    per-customer ordering

Trigger config (Terraform): Kinesis batch size 100, max 3 retries,
bisect-on-error enabled.
"""


def handler(event, context):
    raise NotImplementedError
