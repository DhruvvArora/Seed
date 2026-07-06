# audience-ingress Terraform

Deploys the Audience Ingress Service into us-east-2.

## Resources

- **S3**: `{env}-audience-uploads` (EventBridge notifications on), `{env}-audience-membership`.
- **Kinesis**: `{env}-audience-events` (1 shard, 7-day retention, KMS).
- **DynamoDB**: `{env}-bullseye-audience-metadata-v2`, `{env}-audience-membership`
  (both PAY_PER_REQUEST + `AudienceIDIndex` GSI + PITR + SSE; membership has TTL).
- **Lambda**: `audience-ingest`, `audience-parquet-writer` (pandas layer),
  `audience-reducer` (+ Kinesis event source mapping).
- **Step Functions**: `{env}-audience-ingest` state machine.
- **EventBridge**: rule routing S3 Object Created -> StartExecution.
- **SNS**: `{env}-audience-ingest-alerts` (Step Function failure path).
- **IAM**: one shared Lambda role, one state machine role, one EventBridge role.

## Required variables

| Variable | Notes |
| --- | --- |
| `env` | Resource name prefix (e.g. `dev`). |
| `mci_get_internal_function_arn` | From mci-core output; the invoke target. |
| `mci_get_internal_function_name` | From mci-core output; passed to handlers via env. |
| `mci_function_alias` | Override to `$LATEST` in dev (LIVE alias not deployed). |
| `pandas_layer_arn` | Defaults to the verified us-east-2 / py3.12 ARN. |

## Notes and lessons

- **pandas_layer_arn is region-specific.** The default is the us-east-2 /
  Python 3.12 / x86_64 ARN. Do not reuse an ARN from another region. The version
  suffix (`:29`) can be bumped if a deploy reports the layer version is missing;
  older published versions stay available.
- **S3 cannot start a Step Function directly.** The uploads bucket has
  `eventbridge = true`; an EventBridge rule with an input transformer maps
  bucket+key into the state machine input. The ASL derives tenant/audience from
  the key so no separate trigger Lambda is needed.
- **No reserved concurrency** on any Lambda: dev accounts have a small
  account-level unreserved pool and setting reserved concurrency there fails
  (same lesson as event-ingress).
- **KMS decrypt for the stream** is scoped by a `kms:ViaService` condition on
  `kinesis.{region}.amazonaws.com` rather than a key ARN, because the stream uses
  the AWS-managed `alias/aws/kinesis` key.
- **`terraform validate` runs in your environment**, not in the build sandbox.
  The ASL is validated separately (renders to valid JSON, structural checks pass).

## Teardown

`terraform destroy` after each live session. Empty both S3 buckets first
(`aws s3 rm s3://{env}-audience-uploads --recursive`, same for membership);
buckets with objects block destroy. Budget alert is configured for spend.
