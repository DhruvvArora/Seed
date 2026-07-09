# outbound-egress Terraform

Deploys the Outbound Connector Engine into us-east-2.

## Resources

- **Kinesis**: `{env}-plague` (48 shards, 7-day retention, KMS via
  `alias/aws/kinesis`). Consumed solely by aqueduct-distributor.
- **DynamoDB**: `{env}-connectors`, `{env}-transformations` (both
  PAY_PER_REQUEST + PITR + SSE, no GSI on either).
- **KMS**: one customer-managed key (`{env}-outbound-egress-connector-destination`
  alias) encrypting connector destination configs and integration credentials
  at the application level. Separate from the stream's own
  `alias/aws/kinesis` encryption.
- **Lambda**: `aqueduct-distributor` (+ Kinesis event source mapping, + the
  self-built jq layer), `aqueduct-writer`, `aqueduct-reader`.
- **IAM**: one shared Lambda role, scoped to the union of what all three
  functions need.

## Required variables

| Variable | Notes |
| --- | --- |
| `env` | Resource name prefix (e.g. `dev`). |
| `jq_layer_arn` | ARN of the self-built jq layer. No default; must be built and published before the first apply (see below). |

## Building and publishing the jq layer (do this before `terraform apply`)

1. Build and verify locally:
   ```
   mkdir -p layer/python
   pip install jq --target layer/python
   python3 -c "import sys; sys.path.insert(0, 'layer/python'); import jq; print(jq.compile('.a').input_value({'a': 1}).first())"
   ```
   The PyPI `jq` package ships a self-contained manylinux wheel for cp312
   x86_64 with libjq and oniguruma statically linked (`ldd` on the compiled
   `.so` shows only standard glibc dependencies), so this is a plain `pip
   install`, not a from-source Docker build.
2. Zip and publish:
   ```
   (cd layer && zip -r ../jq-layer.zip .)
   aws lambda publish-layer-version \
     --layer-name outbound-egress-jq \
     --zip-file fileb://jq-layer.zip \
     --compatible-runtimes python3.12 \
     --compatible-architectures x86_64
   ```
3. Take the returned `LayerVersionArn` and pass it as `-var "jq_layer_arn=..."`.

This layer needs to be rebuilt and republished whenever the `jq` package
version changes, or whenever the Lambda runtime moves off cp312/Amazon Linux
2023.

## Notes and lessons

- **No mci-core dependency in the zip.** Unlike mci-backfill, event-ingress,
  and audience-ingress, this package never calls MCI, so the deployment zip
  build is a plain `pip install --target build/ .`, no sibling path install.
- **pyjwt, cryptography, and requests are bundled in the zip**, not a layer,
  since none of the three come from a managed layer and none need a
  from-source build the way jq does.
- **No reserved concurrency** on any of the three Lambdas, same lesson as
  every earlier project: dev accounts have a small account-level unreserved
  pool and setting reserved concurrency there fails deploys.
- **The cross-account `sts:AssumeRole` permission is intentionally broad**
  (`resources = ["*"]`) on this role. The destination account and role name
  are tenant-controlled and only known at connector-config time, not at
  Terraform apply time. Real authorization is enforced by the trust policy
  on the destination account's role, which must allow this role's ARN.
- **`terraform validate` runs in your environment**, not in the build
  sandbox (no registry access there). Do a `terraform fmt` pass locally too;
  the sandbox can't run that either.
- **The plague Kinesis stream's KMS decrypt** is scoped by a `kms:ViaService`
  condition on `kinesis.{region}.amazonaws.com` rather than a key ARN, same
  pattern as audience-ingress and event-ingress, since the stream uses the
  AWS-managed `alias/aws/kinesis` key.

## Manual verification (the spec's "Integration Tests", exercised live)

None of the five projects in this monorepo have real `pytest.mark.integration`
tests; these three scenarios from the spec are meant to be exercised by hand
against real AWS during the deploy-verify-destroy cycle, the same way
audience-ingress's RUNBOOK documents its own manual checks:

1. **Put a BatchProgress event with a test connector to the plague stream,
   verify the webhook is called with the correct payload.** Create a test
   webhook connector via aqueduct-writer's `CreateConnector` pointing at a
   request-catching endpoint (e.g. a temporary webhook.site URL or your own
   test listener), `aws kinesis put-record` a BatchProgress JSON payload
   naming that connector, then confirm the request arrived with the expected
   body and headers.
2. **Disable a connector and verify delivery stops.** Call `UpdateConnector`
   with `enabled: false`, put another BatchProgress event through, confirm no
   request arrives and `connection_status` is not updated.
3. **Create an mParticle integration and verify 3 connector rows exist in
   DynamoDB.** Call `CreateIntegration` with 3 `event_types`, then
   `aws dynamodb query` the connectors table for that tenant and confirm
   exactly 3 rows, each with the `integration` attribute set and the correct
   `integration_name`.

## Teardown

`terraform destroy` after each live session. The KMS key has a 30 day
deletion window by default (AWS minimum 7, this module uses 30); destroying
the Terraform resource schedules deletion, it does not delete the key
immediately.
