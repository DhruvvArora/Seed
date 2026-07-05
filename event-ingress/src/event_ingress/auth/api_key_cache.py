"""DynamoDB API key lookup with an in-memory TTL cache.

The cache exists to satisfy the spec's acceptance criterion: "API key cache
reduces DynamoDB reads by > 90% under sustained load." Under sustained load
from a single warm Lambda container, every request after the first within the
TTL window hits the in-memory dict rather than DynamoDB.

Two-layer caching
-----------------
API Gateway itself caches the authorizer result by identity source value for
`authorizer_result_ttl_in_seconds` (set in Terraform). That reduces Lambda
invocations at the infrastructure level. The in-memory cache here reduces
DynamoDB reads within a warm Lambda container for requests that do reach the
Lambda -- e.g. when the API GW cache TTL is short, or when multiple API GW
instances are active and each has a cold cache.

Thread safety
-------------
Lambda processes one event at a time per container, so the dict operations
here are not concurrent in practice. A threading.Lock is included anyway
because (a) it costs nothing and (b) it makes the behavior safe if Lambda's
execution model ever allows concurrency within a container.

DynamoDB table schema (see Terraform dynamodb.tf)
--------------------------------------------------
  partition_key  (String, hash)  -- the raw API key value
  tenant_id      (String)        -- associated tenant
  enabled        (Boolean)       -- whether key is active
  created_at     (String)        -- ISO timestamp
  name           (String)        -- human-readable label
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import boto3


@dataclass
class CacheEntry:
    tenant_id: str
    expires_at: float  # epoch seconds


class ApiKeyCache:
    """Thin wrapper around a DynamoDB table with an in-memory TTL cache.

    Instantiate once at module scope (per Lambda container). Pass a
    pre-built boto3 DynamoDB resource for testing (moto) or let it
    default to the real client.
    """

    def __init__(
        self,
        table_name: str,
        ttl_seconds: int = 900,
        dynamodb_resource: Any = None,
    ) -> None:
        self._table_name = table_name
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def get_tenant_id(self, api_key: str) -> str | None:
        """Return the tenant_id for a valid, enabled API key, or None.

        Returns None if the key is not found or `enabled` is False.
        A None result from the caller (joust) becomes a Deny IAM policy.
        """
        # 1. Check in-memory cache first.
        with self._lock:
            entry = self._cache.get(api_key)
            if entry is not None and time.monotonic() < entry.expires_at:
                return entry.tenant_id

        # 2. Cache miss or expired -- go to DynamoDB.
        tenant_id = self._fetch_from_dynamo(api_key)
        if tenant_id is not None:
            with self._lock:
                self._cache[api_key] = CacheEntry(
                    tenant_id=tenant_id,
                    expires_at=time.monotonic() + self._ttl_seconds,
                )

        return tenant_id

    def _fetch_from_dynamo(self, api_key: str) -> str | None:
        """GetItem against the apikey-metadata table. Returns None on miss or disabled."""
        response = self._table.get_item(Key={"partition_key": api_key})
        item = response.get("Item")
        if item is None:
            return None
        if not item.get("enabled", False):
            return None
        return str(item["tenant_id"])

    def invalidate(self, api_key: str) -> None:
        """Remove one key from the cache. Useful in tests."""
        with self._lock:
            self._cache.pop(api_key, None)

    def clear(self) -> None:
        """Flush the entire cache. Useful in tests."""
        with self._lock:
            self._cache.clear()
