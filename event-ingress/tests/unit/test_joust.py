"""Unit tests for the joust API key authorizer Lambda.

Named tests from the spec:
  TestJoust_ValidKey          -- valid key returns Allow with tenant_id in context
  TestJoust_InvalidKey        -- unknown key returns Deny
  TestJoust_DisabledKey       -- disabled key returns Deny
  TestJoust_CacheHit          -- second call within TTL does not hit DynamoDB

All tests use moto to mock DynamoDB -- no real AWS calls.
"""

from __future__ import annotations

import boto3
from moto import mock_aws

import event_ingress.handlers.joust as joust_mod
from event_ingress.auth.api_key_cache import ApiKeyCache

TABLE_NAME = "test-apikey-metadata"
REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _create_apikey_table(dynamodb):
    return dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "partition_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "partition_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _put_key(table, api_key: str, tenant_id: str, enabled: bool = True) -> None:
    table.put_item(
        Item={
            "partition_key": api_key,
            "tenant_id": tenant_id,
            "enabled": enabled,
            "name": "test-key",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )


def _fresh_cache(dynamodb_resource) -> ApiKeyCache:
    return ApiKeyCache(
        table_name=TABLE_NAME,
        ttl_seconds=900,
        dynamodb_resource=dynamodb_resource,
    )


_DEFAULT_METHOD_ARN = "arn:aws:execute-api:us-east-1:123:abc/dev/POST/event"


def _joust_event(api_key: str, method_arn: str = _DEFAULT_METHOD_ARN) -> dict:
    return {"authorizationToken": api_key, "methodArn": method_arn}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@mock_aws
def test_valid_key_returns_allow_with_tenant_id(monkeypatch):
    """TestJoust_ValidKey -- valid key resolves to Allow with tenant_id in context."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = _create_apikey_table(dynamodb)
    _put_key(table, "key-abc", "tenant-xyz", enabled=True)

    cache = _fresh_cache(dynamodb)
    monkeypatch.setattr(joust_mod, "_cache", cache)

    result = joust_mod.handler(_joust_event("key-abc"))

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["tenant_id"] == "tenant-xyz"
    assert result["principalId"] == "key-abc"


@mock_aws
def test_unknown_key_returns_deny(monkeypatch):
    """TestJoust_InvalidKey -- unknown key returns Deny, no context."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    _create_apikey_table(dynamodb)

    cache = _fresh_cache(dynamodb)
    monkeypatch.setattr(joust_mod, "_cache", cache)

    result = joust_mod.handler(_joust_event("key-does-not-exist"))

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


@mock_aws
def test_disabled_key_returns_deny(monkeypatch):
    """TestJoust_DisabledKey -- disabled key (enabled=False) returns Deny."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = _create_apikey_table(dynamodb)
    _put_key(table, "key-disabled", "tenant-xyz", enabled=False)

    cache = _fresh_cache(dynamodb)
    monkeypatch.setattr(joust_mod, "_cache", cache)

    result = joust_mod.handler(_joust_event("key-disabled"))

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"


@mock_aws
def test_cache_hit_does_not_hit_dynamodb(monkeypatch):
    """TestJoust_CacheHit -- second call within TTL does not go to DynamoDB."""
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = _create_apikey_table(dynamodb)
    _put_key(table, "key-cached", "tenant-abc", enabled=True)

    cache = _fresh_cache(dynamodb)
    monkeypatch.setattr(joust_mod, "_cache", cache)

    # First call -- populates cache.
    r1 = joust_mod.handler(_joust_event("key-cached"))
    assert r1["policyDocument"]["Statement"][0]["Effect"] == "Allow"

    # Delete the item from DynamoDB to prove the second call never reads it.
    table.delete_item(Key={"partition_key": "key-cached"})

    # Second call -- must still Allow (served from cache, not DynamoDB).
    r2 = joust_mod.handler(_joust_event("key-cached"))
    assert r2["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert r2["context"]["tenant_id"] == "tenant-abc"


def test_missing_authorization_token_returns_deny(monkeypatch):
    """Missing authorizationToken is Deny without hitting DynamoDB."""
    monkeypatch.setattr(joust_mod, "_cache", None)  # Should never be called.
    result = joust_mod.handler({"methodArn": "arn:..."})
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
