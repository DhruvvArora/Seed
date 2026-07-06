# Audience Ingress Service (Project 4)

Brings audience memberships into the platform through two independent paths.

## Paths

**Batch (S3 CSV).** A client uploads a gzipped CSV (`{tenant}/{audience}/{ts}.csv.gz`)
to the uploads bucket. S3 emits the event to EventBridge, which starts the
`audience-ingest` Step Function:

1. `DeriveContext` splits the key into tenant_id / audience_id (ASL intrinsics).
2. `DecompressAndMap` (`audience-ingest` Lambda) downloads, dedups, and resolves
   external IDs to internal UUIDs via MCI, 5000 per invoke.
3. `WriteParquet` (`audience-parquet-writer` Lambda) writes one parquet file to
   the Hive-partitioned membership bucket.
4. `UpdateMetadata` (direct DynamoDB `updateItem`, no Lambda) atomically flips the
   metadata row to `ELIGIBLE` and sets `size` to the real member count.

**Streaming (mParticle).** `audience-reducer` consumes the `audience-events`
Kinesis stream, resolves IDs via MCI, and upserts membership rows: `add` ->
ELIGIBLE, `delete` -> INELIGIBLE (soft delete, never removed). Writes run 50x
parallel.

## The size sentinel

`size = -1` means "created but not loaded." Downstream offer-build treats it as
not-ready. The batch pipeline never writes a partial count: the completion write
is a single `updateItem` at the end of the Step Function, so `size` goes from -1
straight to the final count with no in-between window. `store/metadata.py`
`completion_fields()` refuses a negative size to guard this invariant, and the
ASL sets exactly that field set.

## Parquet dependency

pyarrow/pandas are not in the Lambda zip. Only `audience-parquet-writer` needs
them, and it gets them from the AWS-managed AWSSDKPandas layer
(`var.pandas_layer_arn`, region-specific to us-east-2). The parquet module
imports `awswrangler` lazily so the package still imports and tests still collect
in an environment without the layer.

## Build and deploy

```
# from audience-ingress/
pip install -e ../mci-core -e ".[dev]"
pytest -m "not integration"

# deploy zip: include mci-core, NO editable installs (avoid stale egg-links)
rm -rf build && pip install --target build/ ../mci-core .
(cd build && zip -qr ../build/audience-ingress.zip . \
  -x 'boto3/*' 'botocore/*' 's3transfer/*' 'urllib3/*')

# terraform (us-east-2). LIVE alias deferred: override to $LATEST as in P2/P3.
cd terraform
terraform init && terraform validate
terraform apply \
  -var 'env=dev' \
  -var 'mci_function_alias=$LATEST' \
  -var 'mci_get_internal_function_arn=<from mci-core output>' \
  -var 'mci_get_internal_function_name=dev-get-internal-customer-ids'
```

The live run steps (upload a CSV, exercise the streaming path with a synthetic
record, verify, tear down) are in `scripts/seed_data/RUNBOOK.md`.

## Known limits (honest notes)

- **`new_mappings_created`** is only truthful when `mci_probe_existing = true`,
  which adds a read-only MCI pass. Off by default; when off it reports 0, meaning
  "not measured," not "zero created." The MCI invoker does not distinguish
  created-vs-existing mappings without the probe.
- **Membership TTL** is left unset. Streaming events carry no audience end date,
  so inventing a TTL would be a guess. The `ttl` attribute and table TTL are
  wired; pass a Unix timestamp when an end date exists and rows auto-expire.

## Cross-project follow-ups

- **P3 wiring (deferred, owned by Project 3):** `audience-events` has no live
  producer yet. P3's `mparticle-processing` currently logs-and-skips
  `audience_membership_change_request` batches. To make the streaming path
  end-to-end, that handler must instead extract the customer identity and
  `PutRecord` to `audience-events` in the internal message shape
  (`{tenant_id, external_customer_id, audience_changes:[{audience_id, action}], timestamp}`).
  This is intentionally NOT done here to keep project ownership clean. P4's
  streaming path is tested against `audience-events` directly with a synthetic
  record (see the runbook), which tests the reducer in isolation from the
  producer. Track this as a P3 task; add the TODO to `mparticle-processing` when
  P3 is merged.
