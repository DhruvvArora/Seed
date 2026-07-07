"""DynamoDB access for the connectors table.

Python equivalent of the Go internal/store package's connector operations.
Follows the same shape as mci-core's MciStore: boto3 *resource* interface
(Table object) for plain Python types, low-level client only where needed
(batch_get_item, since the resource Table object does not expose it).
"""

from __future__ import annotations

from typing import Any, cast

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from outbound_egress.model.types import ConnectionStatus, Connector

# DynamoDB hard limit: BatchGetItem accepts at most 100 keys per request.
BATCH_GET_MAX_KEYS = 100


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class ConnectorStore:
    """Thin wrapper over the connectors DynamoDB table."""

    def __init__(self, table_name: str, dynamodb_resource: Any = None) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def get_connectors(self, tenant_id: str, connector_names: list[str]) -> dict[str, Connector]:
        """Batch read connector rows by name for one tenant.

        Returns a dict of connector_name -> Connector for rows that exist.
        Names with no matching row are simply absent from the result (the
        caller logs this rather than treating it as fatal, since a progress
        event's connectors list can reference a connector that was deleted
        after the event was emitted but before this batch was processed).
        """
        found: dict[str, Connector] = {}
        if not connector_names:
            return found

        unique_names = list(dict.fromkeys(connector_names))  # dedupe, preserve order
        for chunk in _chunk(unique_names, BATCH_GET_MAX_KEYS):
            keys = [{"partition_key": tenant_id, "sort_key": name} for name in chunk]
            self._batch_get_with_retry(keys, found)

        return found

    def _batch_get_with_retry(
        self, request_keys: list[dict[str, str]], found: dict[str, Connector]
    ) -> None:
        table_name = self._table.name
        client = self._table.meta.client
        keys_to_fetch = request_keys

        while keys_to_fetch:
            response = client.batch_get_item(RequestItems={table_name: {"Keys": keys_to_fetch}})
            for raw in response.get("Responses", {}).get(table_name, []):
                connector = Connector.from_item(raw)
                found[connector.name] = connector

            unprocessed: Any = response.get("UnprocessedKeys", {}).get(table_name, {})
            keys_to_fetch = unprocessed.get("Keys", []) if unprocessed else []

    def list_connectors(self, tenant_id: str) -> list[Connector]:
        """Rows where the integration attribute is absent: plain webhooks only."""
        return [c for c in self._query_all(tenant_id) if not c.is_integration]

    def list_integrations(self, tenant_id: str) -> list[Connector]:
        """Rows where the integration attribute is present."""
        return [c for c in self._query_all(tenant_id) if c.is_integration]

    def _query_all(self, tenant_id: str) -> list[Connector]:
        items: list[dict[str, Any]] = []
        response = self._table.query(KeyConditionExpression=Key("partition_key").eq(tenant_id))
        items.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            response = self._table.query(
                KeyConditionExpression=Key("partition_key").eq(tenant_id),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        return [Connector.from_item(item) for item in items]

    def create_connector(self, connector: Connector) -> None:
        self._table.put_item(Item=cast("Any", connector.to_item()))

    def delete_connector(self, tenant_id: str, connector_name: str) -> bool:
        """Returns True if a row existed and was removed, False if there was
        nothing to delete."""
        try:
            self._table.delete_item(
                Key={"partition_key": tenant_id, "sort_key": connector_name},
                ConditionExpression="attribute_exists(partition_key)",
            )
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def update_connection_status(
        self, tenant_id: str, connector_name: str, status: ConnectionStatus
    ) -> None:
        self._table.update_item(
            Key={"partition_key": tenant_id, "sort_key": connector_name},
            UpdateExpression="SET connection_status = :cs",
            ExpressionAttributeValues={":cs": cast("Any", status.to_item())},
        )
