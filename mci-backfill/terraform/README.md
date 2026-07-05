# Terraform: MCI Backfill Pipeline (Project 2)

Provisions the tenant registry table, the Athena workgroup and temp bucket, the
four backfill Lambdas, both IAM roles (Lambda execution and Step Function
execution), and the Step Function itself, rendered from
`../step_function/backfill.asl.json`.

## Before you `apply`

Two inputs have no sensible default and must be supplied:

- `mci_get_internal_function_arn` (and `mci_get_internal_function_name`): the
  ARN/name of mci-core's `get-internal-customer-ids` Lambda. Read these from
  the mci-core Terraform module's own outputs.
- `source_data_s3_bucket_arns`: the S3 bucket(s) backing the 4 pre-existing
  Glue tables (`action_events`, `audience_membership`, `ingested_attributes`,
  `transactions`) that `CollectCustomerIDs` reads from. This module does not
  own or create that data, only reads it. Leaving this empty means the IAM
  policies skip the S3 read statement entirely, and `CollectCustomerIDs` will
  fail with an S3 access-denied error even though Athena/Glue permissions are
  otherwise correct, worth remembering if the first live run fails oddly.

## Building the Lambda deployment zip

Unlike mci-core's zip, this one must contain both `mci-backfill` and its
`mci-core` path dependency, since Lambda has no pip install step at runtime:

```bash
pip install --target build/ -e ../mci-core -e .
(cd build && zip -r ../build/mci-backfill.zip .)
```

`var.lambda_zip_path` defaults to `build/mci-backfill.zip`.

## What was validated here, and what was not

No AWS credentials or Terraform registry access were available in the
environment these files were written in, so `terraform init`, `validate`, and
`plan` have not been run. What was checked instead:

- Every `.tf` file parses as syntactically valid HCL (via `python-hcl2`).
- The Step Function's ASL passed `statelint` (an offline structural
  validator) both before and after simulating exactly what `templatefile()`
  does: substituting the four `${...FunctionArn}` placeholders with realistic
  ARNs and re-validating the result. This proves the rendering pipeline itself
  is sound, not just the raw ASL file.

What is still unverified until a real run: that the IAM policies are
sufficient in practice (Athena's actual Glue/S3 access patterns can be fussier
than the docs suggest), that the Lambda zip actually contains both packages
correctly, and that the state machine executes end to end. That is exactly
what the small seeded live run (Athena source tables + Glue table, one
execution, then destroy) is for.

## Layout

```
provider.tf, versions.tf, variables.tf, outputs.tf   # module scaffolding
dynamodb.tf         # tenant registry table
athena.tf           # workgroup + temp bucket (with lifecycle expiry)
lambda.tf           # the 4 backfill Lambdas
iam.tf              # Lambda execution role + Step Function execution role
step_function.tf    # renders and deploys ../step_function/backfill.asl.json
```
