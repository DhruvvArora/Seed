# Outbound Connector Engine live-run runbook

Exercises the plague stream, connector CRUD, and the delivery path against
real AWS in us-east-2, then tears everything down. No other project's
Lambda ARN is needed as an input here (unlike P2 through P4, this package
never calls MCI). Chain credential exports with `&&` so the session token
does not expire between steps.

Set once:

```
export ENV=dev
export AWS_PROFILE=dhruv-dev
export STREAM=$ENV-plague
export CONNECTORS_TABLE=$ENV-connectors
export WRITER_FN=$ENV-aqueduct-writer
export READER_FN=$ENV-aqueduct-reader
```

Before step 0, build and publish the jq layer if you have not already (see
`terraform/README.md` for the exact commands) and note its ARN.

## 0. Deploy

```
cd outbound-egress
rm -rf build && pip install --target build/ .
(cd build && zip -qr ../build/outbound-egress.zip . \
  -x 'boto3/*' 'botocore/*' 's3transfer/*' 'urllib3/*' 'jq*')

cd terraform
terraform init && terraform validate && terraform fmt -check
terraform apply \
  -var "env=$ENV" \
  -var "jq_layer_arn=<layer ARN from the jq layer publish step>"
```

## 1. Create a test connector

Edit `../scripts/seed_data/create_connector_payload.json` first: replace the
placeholder URL with a real request-catching endpoint (a fresh
https://webhook.site URL is the fastest option).

```
cd ../scripts/seed_data
aws lambda invoke --function-name $WRITER_FN \
  --cli-binary-format raw-in-base64-out \
  --payload file://create_connector_payload.json \
  create_connector_response.json
cat create_connector_response.json
```

Confirm the row landed, and that the destination is not plaintext:

```
aws dynamodb get-item --table-name $CONNECTORS_TABLE \
  --key '{"partition_key":{"S":"tenant-demo"},"sort_key":{"S":"DemoWebhook"}}'
```

## 2. Put a BatchProgress event on the plague stream, verify delivery

```
aws kinesis put-record \
  --stream-name $STREAM \
  --partition-key "tenant-demo#uuid-demo-0001" \
  --data fileb://sample_batch_progress.json
```

Check the webhook.site inbox: the request should arrive within a few
seconds, containing the raw `Progress.to_dict()` payload (no
`transformation_name` was set on this connector, so it delivers as-is).
Then confirm `connection_status` was written:

```
aws dynamodb get-item --table-name $CONNECTORS_TABLE \
  --key '{"partition_key":{"S":"tenant-demo"},"sort_key":{"S":"DemoWebhook"}}' \
  --query 'Item.connection_status'
```

Expect `status_code = 200` (or whatever webhook.site returned) and a fresh
timestamp.

## 3. Disable the connector, verify delivery stops

```
aws lambda invoke --function-name $WRITER_FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"operation":"UpdateConnector","input":{"tenant_id":"tenant-demo","name":"DemoWebhook","enabled":false}}' \
  update_response.json

aws kinesis put-record \
  --stream-name $STREAM \
  --partition-key "tenant-demo#uuid-demo-0001" \
  --data fileb://sample_batch_progress.json
```

Watch the distributor's CloudWatch log group
(`/aws/lambda/$ENV-aqueduct-distributor`) for the "disabled, skipping" line,
and confirm no new request arrives at webhook.site and
`connection_status.timestamp` is unchanged from step 2.

## 4. Create an mParticle integration, verify 3 connector rows

Edit `../scripts/seed_data/create_integration_payload.json` with a real test
URL too, then:

```
aws lambda invoke --function-name $WRITER_FN \
  --cli-binary-format raw-in-base64-out \
  --payload file://create_integration_payload.json \
  create_integration_response.json
cat create_integration_response.json   # expect 3 rows in the response array

aws dynamodb query --table-name $CONNECTORS_TABLE \
  --key-condition-expression "partition_key = :pk" \
  --filter-expression "attribute_exists(#i)" \
  --expression-attribute-names '{"#i":"integration"}' \
  --expression-attribute-values '{":pk":{"S":"tenant-demo"}}'
```

Expect exactly 3 items: `DemoMParticleIntegration-READY`,
`DemoMParticleIntegration-ACTIVATED`, `DemoMParticleIntegration-ACHIEVED`.

## 5. aqueduct-reader sanity check

```
aws lambda invoke --function-name $READER_FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"tenant_id":"tenant-demo","connector_names":["DemoWebhook","DoesNotExist"]}' \
  reader_response.json
cat reader_response.json   # DemoWebhook -> summary, DoesNotExist -> null
```

## 6. Teardown

```
aws lambda invoke --function-name $WRITER_FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"operation":"DeleteIntegration","input":{"tenant_id":"tenant-demo","integration_name":"DemoMParticleIntegration"}}' \
  /dev/null
aws lambda invoke --function-name $WRITER_FN \
  --cli-binary-format raw-in-base64-out \
  --payload '{"operation":"DeleteConnector","input":{"tenant_id":"tenant-demo","name":"DemoWebhook"}}' \
  /dev/null

cd ../../terraform
terraform destroy -var "env=$ENV" -var "jq_layer_arn=<same layer ARN as step 0>"
```

The KMS key has a 30 day deletion window; `destroy` schedules deletion, it
does not remove the key immediately. Confirm the AWS Budget alert is still
armed after teardown.
