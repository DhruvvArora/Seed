"""DynamoDB access for the transformations table."""

from __future__ import annotations

from typing import Any

import boto3

from outbound_egress.model.types import Transformation


class TransformationStore:
    """Thin wrapper over the transformations DynamoDB table."""

    def __init__(self, table_name: str, dynamodb_resource: Any = None) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def get_transformation(self, tenant_id: str, name: str) -> Transformation | None:
        response = self._table.get_item(Key={"partition_key": tenant_id, "sort_key": name})
        item = response.get("Item")
        return Transformation.from_item(item) if item else None
