# Project 2 Live Run: Runbook

One real Step Function execution against a tiny seeded dataset, then destroy
everything. Follow this in order; each stage depends on the one before it.

Throughout, `{env}` is whatever you pass as `-var="env=..."` (e.g. `dev`), and
`{database}` is a throwaway Glue database name of your choice (e.g.
`backfill_live_test`). Pick these once and use them consistently below.

## 0. Refresh your session token

Same as every session: `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
AWS_SESSION_TOKEN` then `eval "$(aws configure export-credentials --format env)"`
before any Terraform command.

## 1. Re-deploy mci-core

Project 1's resources were destroyed after that session's cost-hygiene
cleanup. Backfill needs a real, live MCI function to point at.

```bash
cd mci-core/terraform
terraform apply -var="env={env}"
terraform output   # note get_internal_function_name
```

Construct the ARN:
`arn:aws:lambda:us-east-2:<your-account-id>:function:{env}-get-internal-customer-ids`

(Find your account id with `aws sts get-caller-identity --query Account
--output text`.)

## 2. Seed the fake source data

The 4 seed CSVs are in `mci-backfill/scripts/seed_data/`: `action_events.csv`,
`audience_membership.csv`, `ingested_attributes.csv`, `transactions.csv`. They
model 2 real tenants (`t1`: 4 unique customers after dedup, `t2`: 3) plus one
`tenant-test` row, so the run proves both the dedup logic and that test
tenants get filtered out.

Create a throwaway bucket and upload:

```bash
aws s3 mb s3://{env}-backfill-seed-source
aws s3 cp mci-backfill/scripts/seed_data/action_events.csv \
  s3://{env}-backfill-seed-source/source-data/action_events/action_events.csv
aws s3 cp mci-backfill/scripts/seed_data/audience_membership.csv \
  s3://{env}-backfill-seed-source/source-data/audience_membership/audience_membership.csv
aws s3 cp mci-backfill/scripts/seed_data/ingested_attributes.csv \
  s3://{env}-backfill-seed-source/source-data/ingested_attributes/ingested_attributes.csv
aws s3 cp mci-backfill/scripts/seed_data/transactions.csv \
  s3://{env}-backfill-seed-source/source-data/transactions/transactions.csv
```

Create the Glue database, then run each `CREATE EXTERNAL TABLE` statement in
`mci-backfill/scripts/seed_data/create_source_tables.sql` (substitute
`{database}` and `{s3_prefix}` = `s3://{env}-backfill-seed-source/source-data`
into each), either by pasting into the Athena console query editor, or with
`aws athena start-query-execution --query-string "..." --query-execution-context
Database={database} --work-group backfill-workgroup --result-configuration
OutputLocation=s3://{env}-backfill-temp/query-results/` for each of the 4
statements. (The workgroup and temp bucket come from step 3, so do this after
the `mci-backfill` Terraform apply, or point at a different, already-existing
workgroup/bucket for these 4 DDL calls specifically.)

## 3. Deploy mci-backfill

Build the Lambda zip first:

```bash
cd mci-backfill
pip install --target build/ -e ../mci-core -e .
(cd build && zip -r ../build/mci-backfill.zip .)
```

Apply, supplying the values from steps 1 and 2:

```bash
cd terraform
terraform apply \
  -var="env={env}" \
  -var="mci_get_internal_function_name={env}-get-internal-customer-ids" \
  -var="mci_get_internal_function_arn=arn:aws:lambda:us-east-2:<account-id>:function:{env}-get-internal-customer-ids" \
  -var="athena_source_database={database}" \
  -var='source_data_s3_bucket_arns=["arn:aws:s3:::{env}-backfill-seed-source"]'
terraform output   # note state_machine_arn and tenant_registry_table_name
```

## 4. Seed the tenant registry

Terraform created the table but not its rows. Add the two real test tenants
(not `tenant-test`, which should stay absent so its exclusion is meaningful):

```bash
aws dynamodb put-item --table-name {env}-tenant-registry \
  --item '{"tenant_id": {"S": "t1"}, "active": {"BOOL": true}}'
aws dynamodb put-item --table-name {env}-tenant-registry \
  --item '{"tenant_id": {"S": "t2"}, "active": {"BOOL": true}}'
```

## 5. Run it

Edit `mci-backfill/scripts/seed_data/execution_input.json`, replacing every
`{database}` and `{env}` placeholder with your actual values, then:

```bash
aws stepfunctions start-execution \
  --state-machine-arn <state_machine_arn from step 3> \
  --input file://mci-backfill/scripts/seed_data/execution_input.json
```

Poll until it finishes:

```bash
aws stepfunctions describe-execution --execution-arn <arn from the start-execution response>
```

## 6. Verify

Check the MCI table for day-0 mappings. Every id from `t1` and `t2` should
have `internal_customer_id` equal to its own external id; `tenant-test` and
`fake1` should not appear at all.

```bash
aws dynamodb scan --table-name {env}-master-customer-index
```

Expect exactly 7 items: `t1#c1`, `t1#c2`, `t1#c3`, `t1#c4`, `t2#x1`, `t2#x2`,
`t2#x3`. If `tenant-test#fake1` shows up, the CTAS exclusion did not work; if
`t1`/`t2` are missing entirely, check the tenant registry seed (step 4) and
that `EXCLUDED_TENANT_IDS` on `list-tenants` was not accidentally set to
exclude them too.

Also worth a glance: the CloudWatch Logs for `backfill-mci` and
`athena-query-runner`, and the Step Function's own execution graph in the
console, to see the Map state's 2 concurrent tenant branches and the iterator
loop actually running.

## 7. Destroy, in this order

S3 buckets with objects fail to destroy, so empty them first (the lesson from
Project 1 applies here too):

```bash
aws s3 rm s3://{env}-backfill-temp --recursive
aws s3 rm s3://{env}-backfill-seed-source --recursive

cd mci-backfill/terraform
terraform destroy -var="env={env}" \
  -var="mci_get_internal_function_name={env}-get-internal-customer-ids" \
  -var="mci_get_internal_function_arn=arn:aws:lambda:us-east-2:<account-id>:function:{env}-get-internal-customer-ids" \
  -var="athena_source_database={database}" \
  -var='source_data_s3_bucket_arns=["arn:aws:s3:::{env}-backfill-seed-source"]'

aws s3 rb s3://{env}-backfill-seed-source

cd ../../mci-core/terraform
terraform destroy -var="env={env}"
```

Drop the throwaway Glue database and its 4 tables last, either via the Glue
console or `aws glue delete-database --name {database}` (this also removes
its tables).
