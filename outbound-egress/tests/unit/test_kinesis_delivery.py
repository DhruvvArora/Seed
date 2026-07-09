"""Tests for outbound_egress.deliver.kinesis.

Uses moto to mock both STS (assume_role) and Kinesis (put_record) so no real
AWS calls happen. moto's assume_role does not enforce a real trust policy,
which is fine here: the test verifies the delivery module's own logic (role
ARN construction, credential wiring, partition key scheme, error mapping),
not IAM trust policy evaluation, which is Terraform/AWS's responsibility.
"""

import boto3
import pytest
from moto import mock_aws

from outbound_egress.deliver.errors import RetryableDeliveryError
from outbound_egress.deliver.kinesis import deliver_kinesis
from outbound_egress.model.types import KinesisDestination


def _destination(region: str = "us-east-2") -> KinesisDestination:
    return KinesisDestination(
        account_id="111122223333",
        role_name="cross-account-role",
        stream_name="brand-events",
        region=region,
    )


@mock_aws
def test_kinesis_delivery_assumes_cross_account_role_and_puts_record():
    region = "us-east-2"
    kinesis = boto3.client("kinesis", region_name=region)
    kinesis.create_stream(StreamName="brand-events", ShardCount=1)

    result = deliver_kinesis(
        _destination(region),
        tenant_id="tenant-abc",
        customer_id="uuid-1234",
        payload={"offer_id": "offer-1", "status": "ACHIEVED"},
        kinesis_client=kinesis,  # bypass real cross-account creds for the test
    )

    assert result.success is True

    records = kinesis.get_records(
        ShardIterator=kinesis.get_shard_iterator(
            StreamName="brand-events",
            ShardId=kinesis.describe_stream(StreamName="brand-events")["StreamDescription"][
                "Shards"
            ][0]["ShardId"],
            ShardIteratorType="TRIM_HORIZON",
        )["ShardIterator"]
    )["Records"]

    assert len(records) == 1
    assert records[0]["PartitionKey"] == "tenant-abc#uuid-1234"


@mock_aws
def test_kinesis_delivery_uses_tenant_and_customer_partition_key_scheme():
    region = "us-east-2"
    kinesis = boto3.client("kinesis", region_name=region)
    kinesis.create_stream(StreamName="brand-events", ShardCount=1)

    deliver_kinesis(
        _destination(region),
        tenant_id="tenant-xyz",
        customer_id="uuid-9999",
        payload={"offer_id": "offer-2"},
        kinesis_client=kinesis,
    )

    shard_id = kinesis.describe_stream(StreamName="brand-events")["StreamDescription"]["Shards"][0][
        "ShardId"
    ]
    iterator = kinesis.get_shard_iterator(
        StreamName="brand-events", ShardId=shard_id, ShardIteratorType="TRIM_HORIZON"
    )["ShardIterator"]
    records = kinesis.get_records(ShardIterator=iterator)["Records"]

    assert records[0]["PartitionKey"] == "tenant-xyz#uuid-9999"


def test_kinesis_delivery_put_record_failure_raises_retryable_error():
    class _FailingKinesis:
        def put_record(self, **kwargs):
            from botocore.exceptions import ClientError

            raise ClientError(
                {
                    "Error": {
                        "Code": "ProvisionedThroughputExceededException",
                        "Message": "slow down",
                    }
                },
                "PutRecord",
            )

    class _FakeSts:
        def assume_role(self, **kwargs):
            return {
                "Credentials": {
                    "AccessKeyId": "fake",
                    "SecretAccessKey": "fake",
                    "SessionToken": "fake",
                }
            }

    with pytest.raises(RetryableDeliveryError, match="ProvisionedThroughputExceededException"):
        deliver_kinesis(
            _destination(),
            tenant_id="tenant-abc",
            customer_id="uuid-1234",
            payload={"offer_id": "offer-1"},
            sts_client=_FakeSts(),
            kinesis_client=_FailingKinesis(),
        )
