"""Tests for outbound_egress.deliver.oauth.

Uses a real RSA keypair (generated once per test run) so build_and_sign_jwt
and PyJWT's RS256 signing path are exercised for real, not mocked. HTTP
calls (token exchange and delivery) are mocked with `responses`.
"""

import jwt
import pytest
import responses
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from outbound_egress.deliver.errors import NonRetryableDeliveryError, RetryableDeliveryError
from outbound_egress.deliver.oauth import (
    OAuthTokenCache,
    build_and_sign_jwt,
    deliver_oauth,
    exchange_for_access_token,
)
from outbound_egress.model.types import JwtOAuthDestination


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_key = private_key.public_key()
    return private_pem, public_key


def _destination(private_pem: str) -> JwtOAuthDestination:
    return JwtOAuthDestination(
        token_url="https://auth.example.com/token",
        destination_url="https://api.example.com/events",
        signing_key_pem=private_pem,
        jwt_issuer="seed-platform",
        jwt_audience="example-brand",
    )


def test_build_and_sign_jwt_produces_verifiable_rs256_token(rsa_keypair):
    private_pem, public_key = rsa_keypair
    destination = _destination(private_pem)

    token = build_and_sign_jwt(destination)

    decoded = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        audience="example-brand",
    )
    assert decoded["iss"] == "seed-platform"
    assert decoded["exp"] - decoded["iat"] == 3600


@responses.activate
def test_exchange_for_access_token_returns_token_and_ttl(rsa_keypair):
    private_pem, _ = rsa_keypair
    destination = _destination(private_pem)
    responses.add(
        responses.POST,
        "https://auth.example.com/token",
        json={"access_token": "tok-abc", "expires_in": 1800},
        status=200,
    )

    signed_jwt = build_and_sign_jwt(destination)
    access_token, ttl = exchange_for_access_token(destination, signed_jwt)

    assert access_token == "tok-abc"
    assert ttl == 1800


@responses.activate
def test_oauth_delivery_reuses_cached_token_within_ttl(rsa_keypair):
    private_pem, _ = rsa_keypair
    destination = _destination(private_pem)
    cache = OAuthTokenCache()

    responses.add(
        responses.POST,
        "https://auth.example.com/token",
        json={"access_token": "tok-abc", "expires_in": 1800},
        status=200,
    )
    responses.add(responses.POST, "https://api.example.com/events", status=200)
    responses.add(responses.POST, "https://api.example.com/events", status=200)

    deliver_oauth(destination, "tenant-abc", "MParticleOAuth", {"offer_id": "offer-1"}, cache)
    deliver_oauth(destination, "tenant-abc", "MParticleOAuth", {"offer_id": "offer-2"}, cache)

    token_calls = [c for c in responses.calls if c.request.url == "https://auth.example.com/token"]
    assert len(token_calls) == 1  # second delivery reused the cached token


@responses.activate
def test_oauth_delivery_refreshes_token_and_retries_once_on_401(rsa_keypair):
    private_pem, _ = rsa_keypair
    destination = _destination(private_pem)
    cache = OAuthTokenCache()
    cache.set("tenant-abc#MParticleOAuth", "stale-token", ttl_seconds=1800)

    responses.add(
        responses.POST,
        "https://auth.example.com/token",
        json={"access_token": "fresh-token", "expires_in": 1800},
        status=200,
    )
    responses.add(responses.POST, "https://api.example.com/events", status=401)
    responses.add(responses.POST, "https://api.example.com/events", status=200)

    result = deliver_oauth(
        destination, "tenant-abc", "MParticleOAuth", {"offer_id": "offer-1"}, cache
    )

    assert result.success is True
    delivery_calls = [
        c for c in responses.calls if c.request.url == "https://api.example.com/events"
    ]
    assert len(delivery_calls) == 2
    assert delivery_calls[1].request.headers["Authorization"] == "Bearer fresh-token"
    assert cache.get("tenant-abc#MParticleOAuth") == "fresh-token"


@responses.activate
def test_oauth_delivery_raises_non_retryable_on_400_after_refresh(rsa_keypair):
    private_pem, _ = rsa_keypair
    destination = _destination(private_pem)
    cache = OAuthTokenCache()

    responses.add(
        responses.POST,
        "https://auth.example.com/token",
        json={"access_token": "tok-abc", "expires_in": 1800},
        status=200,
    )
    responses.add(responses.POST, "https://api.example.com/events", status=400)

    with pytest.raises(NonRetryableDeliveryError):
        deliver_oauth(destination, "tenant-abc", "MParticleOAuth", {"offer_id": "offer-1"}, cache)


@responses.activate
def test_oauth_delivery_raises_retryable_on_500(rsa_keypair):
    private_pem, _ = rsa_keypair
    destination = _destination(private_pem)
    cache = OAuthTokenCache()

    responses.add(
        responses.POST,
        "https://auth.example.com/token",
        json={"access_token": "tok-abc", "expires_in": 1800},
        status=200,
    )
    responses.add(responses.POST, "https://api.example.com/events", status=503)

    with pytest.raises(RetryableDeliveryError):
        deliver_oauth(destination, "tenant-abc", "MParticleOAuth", {"offer_id": "offer-1"}, cache)
