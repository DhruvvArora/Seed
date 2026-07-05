# MCI Core — Terraform

Infrastructure for Project 1: the DynamoDB table (+ GSI), two S3 buckets, an
IAM role with least-privilege permissions, and the three Lambda functions.

## Files
- `versions.tf`   Terraform + AWS provider version pins
- `variables.tf`  Inputs (env name, region, parallelism factor, zip path)
- `dynamodb.tf`   The master-customer-index table and reverse-lookup GSI
- `s3.tf`         Log bucket + audit bucket (public access blocked)
- `iam.tf`        Lambda execution role + least-privilege policy
- `lambda.tf`     The three functions (get-internal, get-external, forget)
- `outputs.tf`    Names other modules/callers need

## Prerequisites before apply
1. AWS credentials configured (`aws configure` or env vars).
2. A built Lambda zip at the path in `var.lambda_zip_path` (default
   `build/mci-core.zip`). For MCI Core the zip is just the `src/mci` package;
   boto3 is already in the Lambda runtime, so there are no extra deps to bundle.

## Build the Lambda zip (from mci-core/)
```bash
rm -rf build
mkdir build
pip install --target build/ .
cd build && zip -r ../build/mci-core.zip . && cd ..
```

## Validate (no AWS needed, no cost)
```bash
cd terraform
terraform init
terraform validate
terraform fmt -check
```

## Plan (read-only; shows what WOULD be created; needs AWS creds)
```bash
terraform plan -var="env=dev" -var="lambda_zip_path=../build/mci-core.zip"
```

## Apply (CREATES real resources; small pay-per-request cost)
```bash
terraform apply -var="env=dev" -var="lambda_zip_path=../build/mci-core.zip"
```

## Tear down when done experimenting
S3 buckets with objects must be emptied before destroy, or it will fail with
`BucketNotEmpty`:
```bash
aws s3 rm s3://<env>-mci-core-logs --recursive
aws s3 rm s3://<env>-mci-core-audit --recursive
terraform destroy -var="env=dev" -var="lambda_zip_path=../build/mci-core.zip"
```

## Known gap: the `LIVE`/`CANARY` aliases are not built yet

The invoker client (`src/mci/invoker/client.py`) defaults to invoking
`get-internal-customer-ids` at the `LIVE` alias, and Project 1's own spec
describes `LIVE` (production traffic) and `CANARY` (10% of traffic for
testing new versions) as the intended calling convention. This Terraform does
not create either alias: `lambda.tf` never sets `publish = true`, so no
numbered versions exist for an alias to point at.

This surfaced during Project 2's live run: `backfill-mci` invoking MCI at
`:LIVE` failed with `ResourceNotFoundException`, and the workaround for that
run was overriding `mci_function_alias` to `$LATEST`. That override is a test
convenience, not the intended design, and Project 3 and Project 4 will hit
the same gap the same way once they invoke MCI.

Deferred deliberately: aliases exist to make gradual, safe rollouts possible
once there is real traffic and a reason to roll out changes incrementally.
For local development with no deployment pipeline, they add ceremony without
buying anything yet. When this becomes worth building (a real deployment
pipeline, multiple engineers deploying, or actual production traffic), the
fix is small:
1. Set `publish = true` on `aws_lambda_function.get_internal`.
2. Add an `aws_lambda_alias` resource named `LIVE`, pointing at the
   published version.
3. Stop passing `mci_function_alias=$LATEST` overrides in callers; they
   already default to `LIVE` and will pick up the real alias with no code
   changes.
