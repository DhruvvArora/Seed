"""JWT OAuth delivery.

Flow, per the spec:
  1. Build a JWT claim set with a 1 hour expiry
  2. Sign it with RS256 using the connector's configured signing key
  3. Exchange the signed JWT for an access token at the token endpoint
  4. Cache the access token in memory until it expires
  5. POST the transformed payload with the token as a Bearer header
  6. On a 401, refresh the token once and retry the delivery once

Token caching lives in OAuthTokenCache, which is NOT scoped the same way as
DecryptCache (see crypto/kms.py). A cached access token's staleness is
bounded by the token's own `expires_in` value from the auth server, so it is
safe to reuse across invocations the same way ApiKeyCache is: instantiate it
once at module scope in the handler and pass it in. There is no analogue
here to the "operator rotated the config and a warm container serves stale
data with no error" risk that makes DecryptCache invocation-scoped, because
an expired token simply fails with a 401 and gets refreshed, it does not
silently misdeliver.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt
import requests

from outbound_egress.deliver.errors import NonRetryableDeliveryError, RetryableDeliveryError
from outbound_egress.model.types import DeliveryResult, JwtOAuthDestination

JWT_EXPIRY_SECONDS = 3600
TOKEN_EXCHANGE_TIMEOUT_SECONDS = 10.0
DELIVERY_TIMEOUT_SECONDS = 10.0
DEFAULT_TOKEN_TTL_SECONDS = 3600.0


@dataclass
class _TokenEntry:
    access_token: str
    expires_at: float  # epoch seconds, time.monotonic() scale


class OAuthTokenCache:
    """In-memory access token cache, keyed by f"{tenant_id}#{connector_name}".

    Safe to instantiate once at module scope per Lambda container (unlike
    DecryptCache). See the module docstring for why.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, _TokenEntry] = {}
        self._lock = threading.Lock()

    def get(self, cache_key: str) -> str | None:
        with self._lock:
            entry = self._tokens.get(cache_key)
            if entry is None or time.monotonic() >= entry.expires_at:
                return None
            return entry.access_token

    def set(self, cache_key: str, access_token: str, ttl_seconds: float) -> None:
        with self._lock:
            self._tokens[cache_key] = _TokenEntry(
                access_token=access_token,
                expires_at=time.monotonic() + ttl_seconds,
            )

    def invalidate(self, cache_key: str) -> None:
        with self._lock:
            self._tokens.pop(cache_key, None)


def build_and_sign_jwt(destination: JwtOAuthDestination) -> str:
    """Build a claim set with a 1 hour expiry and sign it with RS256."""
    now = int(time.time())
    claims = {
        "iss": destination.jwt_issuer,
        "aud": destination.jwt_audience,
        "iat": now,
        "exp": now + JWT_EXPIRY_SECONDS,
    }
    headers = {"kid": destination.jwt_key_id} if destination.jwt_key_id else None
    encoded = jwt.encode(
        claims,
        destination.signing_key_pem,
        algorithm=destination.jwt_algorithm,
        headers=headers,
    )
    return encoded


def exchange_for_access_token(
    destination: JwtOAuthDestination,
    signed_jwt: str,
    http: Any = None,
) -> tuple[str, float]:
    """POST the signed JWT to the token endpoint. Returns (access_token, ttl_seconds)."""
    client = http or requests

    try:
        response = client.post(
            destination.token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt,
            },
            timeout=TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RetryableDeliveryError(
            f"token exchange with {destination.token_url} failed: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RetryableDeliveryError(
            f"token exchange with {destination.token_url} returned {response.status_code}"
        )

    body = response.json()
    access_token = str(body["access_token"])
    ttl_seconds = float(body.get("expires_in", DEFAULT_TOKEN_TTL_SECONDS))
    return access_token, ttl_seconds


def deliver_oauth(
    destination: JwtOAuthDestination,
    tenant_id: str,
    connector_name: str,
    payload: dict[str, Any],
    token_cache: OAuthTokenCache,
    http: Any = None,
) -> DeliveryResult:
    """Deliver payload to destination.destination_url with a Bearer token.

    Reuses a cached token when present and unexpired. On a 401, refreshes
    the token once and retries the delivery exactly once before giving up.
    """
    client = http or requests
    cache_key = f"{tenant_id}#{connector_name}"

    token = token_cache.get(cache_key)
    if token is None:
        token = _mint_and_cache_token(destination, cache_key, token_cache, client)

    response = _post_with_token(client, destination, payload, token)

    if response.status_code == 401:
        token_cache.invalidate(cache_key)
        token = _mint_and_cache_token(destination, cache_key, token_cache, client)
        response = _post_with_token(client, destination, payload, token)

    if 500 <= response.status_code < 600:
        raise RetryableDeliveryError(
            f"oauth delivery to {destination.destination_url} returned {response.status_code}"
        )

    if 400 <= response.status_code < 500:
        raise NonRetryableDeliveryError(
            f"oauth delivery to {destination.destination_url} returned "
            f"{response.status_code} after a token refresh, connector needs "
            "operator attention"
        )

    return DeliveryResult(success=True, status_code=response.status_code)


def _mint_and_cache_token(
    destination: JwtOAuthDestination,
    cache_key: str,
    token_cache: OAuthTokenCache,
    http: Any,
) -> str:
    signed_jwt = build_and_sign_jwt(destination)
    access_token, ttl_seconds = exchange_for_access_token(destination, signed_jwt, http=http)
    token_cache.set(cache_key, access_token, ttl_seconds)
    return access_token


def _post_with_token(
    http: Any,
    destination: JwtOAuthDestination,
    payload: dict[str, Any],
    token: str,
) -> Any:
    try:
        return http.request(
            destination.method,
            destination.destination_url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RetryableDeliveryError(
            f"oauth delivery to {destination.destination_url} failed: {exc}"
        ) from exc
