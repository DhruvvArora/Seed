# Event Ingress Service (Project 3)

Brings customer events into the platform via two paths:

1. **Direct REST API** (`POST /event`) -- clients call with `x-api-key`; the
   joust authorizer resolves the key to a `tenant_id`; event-enqueue validates,
   calls MCI, and puts to `transactions-internal` or `action-internal`.

2. **mParticle Firehose** (`POST /mparticle`) -- mParticle delivers batched
   events; mparticle-enqueue fast-acks and puts the raw batch to
   `mparticle-enqueue`; mparticle-processing (Kinesis trigger) converts and
   routes to the internal streams.

## Package structure

```
event-ingress/
  pyproject.toml                  -- path dep on mci-core
  src/event_ingress/
    handlers/
      joust.py                    -- API key authorizer Lambda
      event_enqueue.py            -- REST API ingest handler
      mparticle_enqueue.py        -- mParticle fast-ack handler
      mparticle_processing.py     -- Kinesis consumer, converts to InternalEvent
    auth/
      api_key_cache.py            -- DynamoDB lookup + 15-min in-memory TTL cache
    validate/
      event_validator.py          -- pure-Python event validation rules
    convert/
      mparticle.py                -- mParticle format -> InternalEvent + float-to-cents
    kinesis/
      producer.py                 -- PutRecords with retry on throttle
    model/
      types.py                    -- InternalEvent, Transaction, Action, mParticle types
  tests/unit/
    test_joust.py
    test_event_enqueue.py
    test_mparticle.py
  terraform/
    versions.tf, provider.tf, variables.tf
    api_gateway.tf                -- REST API, joust authorizer, two routes
    lambda.tf                     -- all four Lambdas + Kinesis event source mapping
    kinesis.tf                    -- three streams (transactions-internal, action-internal, mparticle-enqueue)
    dynamodb.tf                   -- apikey-metadata table
    iam.tf                        -- Lambda execution role + scoped policies
    outputs.tf
```

## UTC timestamp design note

The REST API receives `event_time` as a naive local datetime (no TZ offset). The
platform cannot derive true UTC without a per-tenant timezone it does not yet have.

Every event written to Kinesis carries:

| field | value |
|---|---|
| `event_local_datetime` | as received from the client |
| `event_utc_datetime` | same as `event_local_datetime` (best available) |
| `event_edge_datetime` | real UTC when this Lambda processed the event |
| `event_attributes["utc_source"]` | `"assumed_local"` |

Downstream consumers must check `utc_source` before treating `event_utc_datetime`
as authoritative. mParticle events do NOT get the marker -- mParticle's
`timestamp_ms` is true epoch UTC, so `event_utc_datetime` is real for those.

## mParticle audience events

`audience_membership_change_request` batches arriving at mparticle-processing are
logged and skipped. Project 4 wires the real `audience-events` Kinesis put when
it builds the audience-reducer.

## Build and validate (no AWS needed)

```bash
# From event-ingress/
pip install -e ../mci-core -e ".[dev]"
pytest -m "not integration"       # 31 unit tests
ruff check src tests
ruff format --check src tests
mypy src

cd terraform
terraform init
terraform validate
terraform fmt -check
```

## Live run runbook

### Prerequisites

- mci-core deployed in the same region/env (it must exist for MCI Lambda invocation)
- `aws login` active (dhruv-dev, AdministratorAccess)
- Terraform state from mci-core run so you have the function name and ARN

### Step 1: seed a test API key into the apikey-metadata table

After `terraform apply` creates the table, insert a test key:

```bash
aws dynamodb put-item \
  --table-name dev-apikey-metadata \
  --item '{
    "partition_key": {"S": "test-key-abc"},
    "tenant_id":     {"S": "tenant-test"},
    "enabled":       {"BOOL": true},
    "name":          {"S": "local-test-key"},
    "created_at":    {"S": "2026-01-01T00:00:00+00:00"}
  }'
```

### Step 2: build the Lambda zip

```bash
# From event-ingress/
rm -rf build && mkdir build
pip install --target build/ ../mci-core .
cd build && zip -r ../build/event-ingress.zip . && cd ..
```

Note: the zip must NOT use `-e` (editable) flags. Editable installs write `.pth`
files pointing back to source directories that do not exist inside Lambda.

### Step 3: deploy

```bash
cd terraform
terraform apply \
  -var="env=dev" \
  -var="lambda_zip_path=../build/event-ingress.zip" \
  -var="mci_get_internal_function_name=dev-get-internal-customer-ids" \
  -var="mci_get_internal_function_arn=arn:aws:lambda:us-east-2:<account>:function:dev-get-internal-customer-ids" \
  -var="mci_function_alias=\$LATEST"
```

The `mci_function_alias=\$LATEST` override is the same workaround used in
Project 2. The LIVE alias does not yet exist as a real AWS resource in mci-core.

### Step 4: smoke test POST /event

Get the endpoint from Terraform output:

```bash
terraform output post_event_url
```

Send a test transaction:

```bash
curl -s -X POST "<endpoint>/event" \
  -H "x-api-key: test-key-abc" \
  -H "Content-Type: application/json" \
  -d '{
    "event_id":   "smoke-test-001",
    "customer_id": "ext-cust-smoke",
    "event_time": "2026-01-15T14:30:00",
    "event_type": "transaction",
    "transaction": {
      "total": 4750,
      "currency": "USD",
      "items": {
        "item-1": {"category": "grocery", "price": 4750, "qty": 1}
      }
    }
  }'
# Expected: {"status": "ok", "event_id": "smoke-test-001"}
```

### Step 5: verify the event landed in Kinesis

```bash
STREAM=$(terraform output -raw transactions_stream_name)
SHARD=$(aws kinesis list-shards --stream-name "$STREAM" \
  --query 'Shards[0].ShardId' --output text)
ITER=$(aws kinesis get-shard-iterator \
  --stream-name "$STREAM" \
  --shard-id "$SHARD" \
  --shard-iterator-type TRIM_HORIZON \
  --query 'ShardIterator' --output text)
aws kinesis get-records --shard-iterator "$ITER" \
  --query 'Records[*].Data' --output text \
  | base64 -d | python3 -m json.tool
```

You should see an InternalEvent with `customer_id` as an internal UUID (not
`ext-cust-smoke`) and `event_attributes.utc_source = "assumed_local"`.

### Step 6: tear down

```bash
terraform destroy \
  -var="env=dev" \
  -var="lambda_zip_path=../build/event-ingress.zip" \
  -var="mci_get_internal_function_name=dev-get-internal-customer-ids" \
  -var="mci_get_internal_function_arn=arn:aws:lambda:us-east-2:<account>:function:dev-get-internal-customer-ids" \
  -var="mci_function_alias=\$LATEST"
```

Kinesis streams and the DynamoDB table are automatically destroyed. No manual
emptying needed (unlike S3 buckets in P1/P2).

## Known gap: LIVE alias

Same documented gap as mci-core and mci-backfill. Override `mci_function_alias`
to `$LATEST` for the live run. See `mci-core/terraform/README.md` for the full
deferred-alias note.
