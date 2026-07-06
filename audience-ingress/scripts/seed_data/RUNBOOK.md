# Audience Ingress live-run runbook

Exercises both paths against real AWS in us-east-2, then tears everything down.
Assumes mci-core is deployed and its `get-internal-customer-ids` function
name/ARN are known. Chain credential exports with `&&` so the session token
does not expire between steps.

Set once:

```
export ENV=dev
export AWS_PROFILE=dhruv-dev
export UPLOADS=$ENV-audience-uploads
export MEMBERSHIP=$ENV-audience-membership
export STREAM=$ENV-audience-events
export META=$ENV-bullseye-audience-metadata-v2
export MEMB_TABLE=$ENV-audience-membership
```

## 0. Deploy

```
cd audience-ingress
rm -rf build && pip install --target build/ ../mci-core .
(cd build && zip -qr ../build/audience-ingress.zip . \
  -x 'boto3/*' 'botocore/*' 's3transfer/*' 'urllib3/*')

cd terraform
terraform init && terraform validate
terraform apply \
  -var "env=$ENV" \
  -var 'mci_function_alias=$LATEST' \
  -var "mci_get_internal_function_arn=<mci-core arn>" \
  -var "mci_get_internal_function_name=$ENV-get-internal-customer-ids"
```

## 1. Batch path (S3 -> Step Function)

The key path encodes tenant and audience: `{tenant}/{audience}/{ts}.csv.gz`.

```
cd ../scripts/seed_data
gzip -kf sample_audience.csv    # -> sample_audience.csv.gz
aws s3 cp sample_audience.csv.gz \
  s3://$UPLOADS/tenant-demo/aud-holiday-q4/20260705T180000.csv.gz
```

Watch the execution:

```
SM=$(aws stepfunctions list-state-machines \
  --query "stateMachines[?name=='$ENV-audience-ingest'].stateMachineArn" --output text)
aws stepfunctions list-executions --state-machine-arn "$SM" --max-results 1
```

Verify results (5 unique members after dedup):

```
# metadata flipped to ELIGIBLE with size = 5, not -1
aws dynamodb get-item --table-name $META \
  --key '{"partition_key":{"S":"tenant-demo#aud-holiday-q4"},"sort_key":{"S":"METADATA"}}'

# parquet landed in the Hive path
aws s3 ls s3://$MEMBERSHIP/tenant_id=tenant-demo/ --recursive
```

## 2. Streaming path (synthetic record -> reducer)

`audience-events` has no live producer yet (see the P3 follow-up in the project
README), so put a record on the stream directly to test the reducer in isolation:

```
aws kinesis put-record \
  --stream-name $STREAM \
  --partition-key tenant-demo \
  --data fileb://synthetic_audience_event.json
```

Verify the membership row was written ELIGIBLE. The internal id is whatever MCI
resolved `ext-cust-100` to; scan the audience partition to find it:

```
aws dynamodb query --table-name $MEMB_TABLE \
  --key-condition-expression "partition_key = :pk" \
  --expression-attribute-values '{":pk":{"S":"tenant-demo#aud-streaming-test"}}'
```

To confirm the soft delete, send the same record with `"action": "delete"` and
re-query: the row stays, `state` becomes `INELIGIBLE`.

## 3. Teardown

```
aws s3 rm s3://$UPLOADS --recursive
aws s3 rm s3://$MEMBERSHIP --recursive
cd ../../terraform
terraform destroy -var "env=$ENV" -var 'mci_function_alias=$LATEST' \
  -var "mci_get_internal_function_arn=<mci-core arn>" \
  -var "mci_get_internal_function_name=$ENV-get-internal-customer-ids"
```

Buckets must be emptied first or `destroy` blocks on them. Confirm the AWS
Budget alert is still armed after teardown.
