"""Tests for outbound_egress.store.transformations."""

import boto3
import pytest
from moto import mock_aws

from outbound_egress.model.types import Transformation
from outbound_egress.store.transformations import TransformationStore

TABLE_NAME = "test-transformations"


@pytest.fixture
def table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-2")
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "partition_key", "KeyType": "HASH"},
                {"AttributeName": "sort_key", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "partition_key", "AttributeType": "S"},
                {"AttributeName": "sort_key", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield boto3.resource("dynamodb", region_name="us-east-2")


def test_get_transformation_returns_row_when_present(table):
    resource_table = table.Table(TABLE_NAME)
    resource_table.put_item(
        Item=Transformation(
            tenant_id="tenant-abc", name="mparticle-s2s", jq_script="{event: .offer_id}"
        ).to_item()
    )

    store = TransformationStore(TABLE_NAME, dynamodb_resource=table)
    found = store.get_transformation("tenant-abc", "mparticle-s2s")

    assert found is not None
    assert found.jq_script == "{event: .offer_id}"


def test_get_transformation_returns_none_when_absent(table):
    store = TransformationStore(TABLE_NAME, dynamodb_resource=table)
    assert store.get_transformation("tenant-abc", "does-not-exist") is None
