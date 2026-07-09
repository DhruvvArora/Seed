"""Tests for outbound_egress.crypto.kms.

Covers the spec's KMS acceptance criterion (encrypt followed by decrypt
returns the original plaintext) plus the decrypt cache behavior decided in
chat: repeated decrypts of the same connector within one DecryptCache
instance hit the cache instead of calling KMS again, and two different
DecryptCache instances never share state (the invocation-scoping guarantee).
"""

import boto3
import pytest
from moto import mock_aws

from outbound_egress.crypto.kms import DecryptCache, DestinationCrypto


@pytest.fixture
def kms_key_id():
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-2")
        response = client.create_key(Description="test key for outbound-egress")
        yield response["KeyMetadata"]["KeyId"]


def test_kms_encrypt_then_decrypt_returns_original_plaintext(kms_key_id):
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-2")
        crypto = DestinationCrypto(kms_client=client)

        plaintext = {"url": "https://example.com/hook", "method": "POST", "headers": {}}
        ciphertext = crypto.encrypt(kms_key_id, plaintext)

        assert ciphertext != plaintext
        assert crypto.decrypt(ciphertext) == plaintext


def test_decrypt_cache_hits_cache_on_second_call_for_same_connector(kms_key_id):
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-2")
        crypto = DestinationCrypto(kms_client=client)
        plaintext = {"url": "https://example.com/hook"}
        ciphertext = crypto.encrypt(kms_key_id, plaintext)

        cache = DecryptCache()
        first = cache.get_or_decrypt(crypto, "tenant-abc", "RetailBrandWebhook", ciphertext)
        second = cache.get_or_decrypt(crypto, "tenant-abc", "RetailBrandWebhook", ciphertext)

        assert first == plaintext
        assert second == plaintext
        # Only one distinct connector was ever decrypted, despite two calls.
        assert len(cache) == 1


def test_decrypt_cache_distinguishes_connectors_by_tenant_and_name(kms_key_id):
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-2")
        crypto = DestinationCrypto(kms_client=client)
        ciphertext_a = crypto.encrypt(kms_key_id, {"url": "https://a.example.com"})
        ciphertext_b = crypto.encrypt(kms_key_id, {"url": "https://b.example.com"})

        cache = DecryptCache()
        cache.get_or_decrypt(crypto, "tenant-abc", "ConnectorA", ciphertext_a)
        cache.get_or_decrypt(crypto, "tenant-abc", "ConnectorB", ciphertext_b)
        # Same connector name, different tenant: must not collide with tenant-abc's entry.
        cache.get_or_decrypt(crypto, "tenant-xyz", "ConnectorA", ciphertext_b)

        assert len(cache) == 3


def test_decrypt_cache_instances_do_not_share_state(kms_key_id):
    """Simulates two separate Lambda invocations: each must get its own cache."""
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-2")
        crypto = DestinationCrypto(kms_client=client)
        ciphertext = crypto.encrypt(kms_key_id, {"url": "https://example.com/hook"})

        invocation_one_cache = DecryptCache()
        invocation_one_cache.get_or_decrypt(crypto, "tenant-abc", "RetailBrandWebhook", ciphertext)

        invocation_two_cache = DecryptCache()
        assert len(invocation_two_cache) == 0
