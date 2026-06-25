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
mkdir -p terraform/mci-core/build
cd src && zip -r ../terraform/mci-core/build/mci-core.zip mci && cd ..
```

## Validate (no AWS needed, no cost)
```bash
cd terraform/mci-core
terraform init
terraform validate
terraform fmt -check
```

## Plan (read-only; shows what WOULD be created; needs AWS creds)
```bash
terraform plan -var="env=dev"
```

## Apply (CREATES real resources; small pay-per-request cost)
```bash
terraform apply -var="env=dev"
```

## Tear down when done experimenting
```bash
terraform destroy -var="env=dev"
```
