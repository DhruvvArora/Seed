"""DynamoDB access for the master-customer-index table.

Python equivalent of the Go `internal/store/dynamodb` package. Implements the
two operations the spec requires for identity resolution:

  * batch_get  -> read up to 100 keys per BatchGetItem call
  * put_if_absent -> create a mapping atomically with a condition expression,
                     handling the race where two callers create the same key
                     at once (the loser re-reads the winner's UUID)

We use the boto3 *resource* interface (Table object) rather than the low-level
client, because it accepts and returns plain Python types instead of the
verbose {"S": "..."} attribute descriptors. That keeps this code readable.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import boto3
from botocore.exceptions import ClientError

from mci.model.types import (
    SORT_KEY_PLACEHOLDER,
    AuditEvent,
    CustomerKey,
    MciItem,
    build_partition_key,
)

# DynamoDB hard limit: BatchGetItem accepts at most 100 keys per request.
BATCH_GET_MAX_KEYS = 100

# GSI for reverse lookups (internal UUID -> external id), defined in the spec.
INTERNAL_ID_GSI = "internal-customer-id-index"


def _chunk(items: list, size: int) -> list[list]:
    """Split a list into chunks of at most `size`."""
    return [items[i : i + size] for i in range(0, len(items), size)]


class MciStore:
    """Thin wrapper over the MCI DynamoDB table."""

    def __init__(self, table_name: str, dynamodb_resource=None) -> None:
        # Allow injecting a resource for tests (moto); default to real boto3.
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def batch_get(self, keys: list[CustomerKey]) -> dict[str, MciItem]:
        """Look up many keys. Returns a dict of partition_key -> MciItem for the
        keys that already exist. Missing keys are simply absent from the result.

        Internally splits into BatchGetItem calls of 100 keys each and retries
        any UnprocessedKeys that DynamoDB hands back under load.
        """
        found: dict[str, MciItem] = {}
        if not keys:
            return found

        # Deduplicate by partition key so we never request the same key twice
        # in one BatchGetItem call (DynamoDB rejects duplicates within a batch).
        unique: dict[str, CustomerKey] = {
            build_partition_key(k.tenant_id, k.customer_id): k for k in keys
        }

        for chunk in _chunk(list(unique.keys()), BATCH_GET_MAX_KEYS):
            request_keys = [{"partition_key": pk, "sort_key": SORT_KEY_PLACEHOLDER} for pk in chunk]
            self._batch_get_with_retry(request_keys, found)

        return found

    def _batch_get_with_retry(self, request_keys: list[dict], found: dict[str, MciItem]) -> None:
        table_name = self._table.name
        client = self._table.meta.client
        keys_to_fetch = request_keys

        # Loop until DynamoDB returns no more UnprocessedKeys.
        while keys_to_fetch:
            response = client.batch_get_item(RequestItems={table_name: {"Keys": keys_to_fetch}})
            for raw in response.get("Responses", {}).get(table_name, []):
                item = MciStore._deserialize(raw)
                found[item.partition_key] = item

            unprocessed: Any = response.get("UnprocessedKeys", {}).get(table_name, {})
            keys_to_fetch = unprocessed.get("Keys", []) if unprocessed else []

    def put_if_absent(self, key: CustomerKey, read_only: bool = False) -> str:
        """Resolve one key to an internal UUID, creating the mapping if needed.

        Returns the internal_customer_id. The flow:
          1. If read_only, only read; raise if the mapping does not exist.
          2. Otherwise generate a fresh UUID and attempt a conditional PutItem
             that only succeeds if the partition_key does not already exist.
          3. If the condition fails, another caller won the race: re-read and
             return the UUID they wrote. This is what makes concurrent writes
             for the same key safe (no duplicate UUIDs).
        """
        pk = build_partition_key(key.tenant_id, key.customer_id)

        existing = self._get_one(pk)
        if existing is not None:
            return existing.internal_customer_id

        if read_only:
            raise KeyError(f"No mapping for {pk} and read_only=True")

        new_uuid = str(uuid.uuid4())
        item = MciItem(
            tenant_id=key.tenant_id,
            external_customer_id=key.customer_id,
            internal_customer_id=new_uuid,
            events=[AuditEvent(action="create_item")],
        )

        try:
            self._table.put_item(
                Item=cast("Any", item.to_item()),
                ConditionExpression="attribute_not_exists(partition_key)",
            )
            return new_uuid
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Lost the race. Re-read to get the winner's UUID.
                winner = self._get_one(pk)
                if winner is not None:
                    return winner.internal_customer_id
            raise

    def query_by_internal_id(self, tenant_id: str, internal_customer_id: str) -> str | None:
        """Reverse lookup: internal UUID -> external id, via the GSI.

        The main table is keyed by partition_key (tenant#external), so it cannot
        be searched by internal UUID directly. The GSI is a second index keyed on
        internal_customer_id, which makes this query possible. Returns the
        external id, or None if no mapping has that UUID for this tenant.
        """
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            IndexName=INTERNAL_ID_GSI,
            KeyConditionExpression=(
                Key("internal_customer_id").eq(internal_customer_id)
                & Key("tenant_id").eq(tenant_id)
            ),
        )
        items = response.get("Items", [])
        if not items:
            return None
        return str(items[0]["external_customer_id"])

    def delete(self, tenant_id: str, external_customer_id: str) -> bool:
        """Delete one mapping. Returns True if a row existed and was removed,
        False if there was nothing to delete.

        Uses a condition expression so the delete only "counts" when the row
        actually existed; that lets us report deleted vs not_found accurately.
        """
        pk = build_partition_key(tenant_id, external_customer_id)
        try:
            self._table.delete_item(
                Key={"partition_key": pk, "sort_key": SORT_KEY_PLACEHOLDER},
                ConditionExpression="attribute_exists(partition_key)",
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False  # nothing there to delete
            raise

    def _get_one(self, partition_key: str) -> MciItem | None:
        response = self._table.get_item(
            Key={"partition_key": partition_key, "sort_key": SORT_KEY_PLACEHOLDER}
        )
        raw = response.get("Item")
        return MciStore._deserialize(raw) if raw else None

    @staticmethod
    def _deserialize(raw: dict) -> MciItem:
        return MciItem.from_item(raw)
