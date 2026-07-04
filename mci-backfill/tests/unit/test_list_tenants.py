"""Unit tests for the list-tenants Lambda and its tenant-registry store.

Uses moto to mock the registry table. The store function is tested directly by
injecting the mocked resource; the handler is tested the way mci-core does it,
by pointing boto3 at moto under mock_aws and monkeypatching the module config.
"""

from __future__ import annotations

import boto3
from moto import mock_aws

import backfill.handlers.list_tenants as h
from backfill.store.tenant_registry import list_active_tenants

TABLE_NAME = "test-tenant-registry"


def _create_registry(dynamodb):
    dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "tenant_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "tenant_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _put(dynamodb, tenant_id, active=None):
    item: dict = {"tenant_id": tenant_id}
    if active is not None:
        item["active"] = active
    dynamodb.Table(TABLE_NAME).put_item(Item=item)


# ----- store tests -----------------------------------------------------------


@mock_aws
def test_returns_all_tenants_sorted():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_registry(dynamodb)
    _put(dynamodb, "tenant-xyz")
    _put(dynamodb, "tenant-abc")

    result = list_active_tenants(TABLE_NAME, excluded_ids=[], dynamodb_resource=dynamodb)
    assert result == ["tenant-abc", "tenant-xyz"]  # sorted, both included


@mock_aws
def test_excludes_well_known_ids():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_registry(dynamodb)
    _put(dynamodb, "tenant-abc")
    _put(dynamodb, "tenant-test")
    _put(dynamodb, "tenant-internal")

    result = list_active_tenants(
        TABLE_NAME,
        excluded_ids=["tenant-test", "tenant-internal"],
        dynamodb_resource=dynamodb,
    )
    assert result == ["tenant-abc"]  # test/internal filtered out


@mock_aws
def test_excludes_explicitly_inactive_but_keeps_default_and_true():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_registry(dynamodb)
    _put(dynamodb, "tenant-active", active=True)
    _put(dynamodb, "tenant-default")  # no active attribute -> treated active
    _put(dynamodb, "tenant-off", active=False)

    result = list_active_tenants(TABLE_NAME, excluded_ids=[], dynamodb_resource=dynamodb)
    assert result == ["tenant-active", "tenant-default"]  # 'off' dropped


@mock_aws
def test_empty_registry_returns_empty_list():
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_registry(dynamodb)

    result = list_active_tenants(TABLE_NAME, excluded_ids=[], dynamodb_resource=dynamodb)
    assert result == []


# ----- handler test ----------------------------------------------------------


@mock_aws
def test_handler_returns_tenants_shape(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _create_registry(dynamodb)
    _put(dynamodb, "tenant-abc")
    _put(dynamodb, "tenant-test")

    monkeypatch.setattr(h, "TENANT_TABLE_NAME", TABLE_NAME)
    monkeypatch.setattr(h, "EXCLUDED_TENANT_IDS", ["tenant-test"])

    out = h.handler({})
    assert out == {"tenants": ["tenant-abc"]}
