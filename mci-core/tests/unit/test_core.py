"""Unit tests for the MCI core: models, thread pool, and DynamoDB store.

These cover the spec's named test cases:
  TestGetInternalCustomerIDs_NewMapping
  TestGetInternalCustomerIDs_ExistingMapping
  TestGetInternalCustomerIDs_RaceCondition
  TestGetInternalCustomerIDs_ReadOnly
  TestGetInternalCustomerIDs_Deduplication (at the store level)
"""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from mci.model.types import AuditEvent, CustomerKey, MciItem, build_partition_key
from mci.parallel.pool import run_parallel
from mci.store.dynamodb.table import MciStore

TABLE_NAME = "test-master-customer-index"


# ----- model tests -----------------------------------------------------------


def test_partition_key_format():
    assert build_partition_key("tenant-abc", "cust-1") == "tenant-abc#cust-1"


def test_mci_item_round_trip():
    item = MciItem(
        tenant_id="t1",
        external_customer_id="ext1",
        internal_customer_id="uuid-1",
        events=[AuditEvent(action="create_item", time="2026-01-01T00:00:00+00:00")],
    )
    serialized = item.to_item()
    assert serialized["partition_key"] == "t1#ext1"
    assert serialized["sort_key"] == "NULL"
    restored = MciItem.from_item(serialized)
    assert restored.internal_customer_id == "uuid-1"
    assert restored.events[0].action == "create_item"


# ----- pool tests ------------------------------------------------------------


def test_run_parallel_preserves_order():
    result = run_parallel([1, 2, 3, 4, 5], lambda x: x * 10, workers=3)
    assert result == [10, 20, 30, 40, 50]


def test_run_parallel_empty():
    assert run_parallel([], lambda x: x) == []


def test_run_parallel_propagates_errors():
    def boom(x):
        if x == 3:
            raise ValueError("kaboom")
        return x

    with pytest.raises(ValueError, match="kaboom"):
        run_parallel([1, 2, 3], boom)


# ----- store tests (moto-mocked DynamoDB) ------------------------------------


def _create_table(dynamodb):
    dynamodb.create_table(
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


@mock_aws
def test_put_if_absent_new_mapping():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    uuid1 = store.put_if_absent(CustomerKey("t1", "ext1"))
    assert uuid1  # got a UUID back


@mock_aws
def test_put_if_absent_existing_mapping_is_stable():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    first = store.put_if_absent(CustomerKey("t1", "ext1"))
    second = store.put_if_absent(CustomerKey("t1", "ext1"))
    assert first == second  # same key always resolves to the same UUID


@mock_aws
def test_read_only_raises_for_missing():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    with pytest.raises(KeyError):
        store.put_if_absent(CustomerKey("t1", "missing"), read_only=True)


@mock_aws
def test_batch_get_returns_only_existing():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    store.put_if_absent(CustomerKey("t1", "ext1"))
    store.put_if_absent(CustomerKey("t1", "ext2"))

    found = store.batch_get(
        [CustomerKey("t1", "ext1"), CustomerKey("t1", "ext2"), CustomerKey("t1", "ext3")]
    )
    assert build_partition_key("t1", "ext1") in found
    assert build_partition_key("t1", "ext2") in found
    assert build_partition_key("t1", "ext3") not in found  # never created


@mock_aws
def test_concurrent_same_key_no_duplicate_uuid():
    """The race-condition guarantee: many threads resolving the SAME key
    concurrently must all get the SAME UUID (no duplicates created)."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    key = CustomerKey("t1", "hot-key")
    results = run_parallel(range(20), lambda _: store.put_if_absent(key), workers=20)

    assert len(set(results)) == 1  # exactly one UUID across all 20 threads


@mock_aws
def test_put_if_absent_honors_supplied_internal_id():
    """A caller-supplied internal id is used verbatim when creating a mapping."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    # Day-0 style: internal id equals the external id.
    resolved = store.put_if_absent(CustomerKey("t1", "ext1"), internal_id_override="ext1")
    assert resolved == "ext1"


@mock_aws
def test_supplied_internal_id_never_repoints_existing_mapping():
    """The override only affects CREATE. An established identity is immutable:
    supplying a different internal id for an existing key returns the original."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_table(dynamodb)
    store = MciStore(TABLE_NAME, dynamodb_resource=dynamodb)

    original = store.put_if_absent(CustomerKey("t1", "ext1"))  # fresh UUID
    again = store.put_if_absent(CustomerKey("t1", "ext1"), internal_id_override="something-else")
    assert again == original  # override ignored; existing mapping wins
