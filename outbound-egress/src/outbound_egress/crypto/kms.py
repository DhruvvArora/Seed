"""KMS encrypt/decrypt for the connector destination field.

Two things live here, and they have very different lifetimes:

DestinationCrypto
------------------
A thin wrapper over the KMS client. Safe (and recommended, for warm-start
connection reuse) to instantiate once at module scope in a handler, the same
way ApiKeyCache is instantiated at module scope in event_ingress.auth --
the boto3 client itself carries no per-invocation state.

DecryptCache
------------
NOT like ApiKeyCache. This class must be instantiated FRESH inside the
aqueduct-distributor handler function body, once per invocation, and never
hoisted to module scope.

Why the difference: ApiKeyCache's 15 minute TTL is an acceptable staleness
window for "is this API key still enabled." A connector's destination config
has no such tolerance. If aqueduct-writer updates a connector (say, an
operator rotates a webhook URL or a signing key) and a warm
aqueduct-distributor container still holds the old plaintext in a
module-scope cache, every event on that container until it's recycled would
misdeliver to the wrong destination or with the wrong credentials, with no
error to signal it. So there is no TTL here at all; instead the cache exists
only inside a single invocation and is discarded when the handler returns.
The cost of this is a full re-decrypt for connectors seen across separate
invocations; that is fine because the whole point of the cache is only to
avoid re-decrypting the SAME connector's config more than once WITHIN one
batch (one invocation), not across invocations.

Decryption doesn't need a key ID (the ciphertext blob carries its own key
metadata); encryption does.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import boto3


class DestinationCrypto:
    """Encrypt/decrypt the connector `destination` field via KMS."""

    def __init__(self, kms_client: Any = None) -> None:
        # Allow injecting a client for tests (moto); default to real boto3.
        self._client = kms_client or boto3.client("kms")

    def encrypt(self, kms_key_id: str, plaintext: dict[str, Any]) -> str:
        """Encrypt a destination config dict. Returns base64 ciphertext,
        which is what gets stored in the connector's `destination` field."""
        response = self._client.encrypt(
            KeyId=kms_key_id,
            Plaintext=json.dumps(plaintext).encode("utf-8"),
        )
        return base64.b64encode(response["CiphertextBlob"]).decode("ascii")

    def decrypt(self, ciphertext_b64: str) -> dict[str, Any]:
        """Decrypt a stored `destination` field back to its plaintext dict."""
        blob = base64.b64decode(ciphertext_b64)
        response = self._client.decrypt(CiphertextBlob=blob)
        plaintext_bytes = response["Plaintext"]
        return dict(json.loads(plaintext_bytes.decode("utf-8")))


class DecryptCache:
    """Invocation-scoped decrypt cache. Create one of these per Lambda
    invocation (inside the handler function body), never at module scope.

    Usage in aqueduct-distributor:

        def handler(event, context):
            cache = DecryptCache()  # fresh every invocation, on purpose
            ...
            destination_json = cache.get_or_decrypt(
                crypto, connector.tenant_id, connector.name, connector.destination
            )
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    def get_or_decrypt(
        self,
        crypto: DestinationCrypto,
        tenant_id: str,
        connector_name: str,
        ciphertext_b64: str,
    ) -> dict[str, Any]:
        cache_key = f"{tenant_id}#{connector_name}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        plaintext = crypto.decrypt(ciphertext_b64)
        self._cache[cache_key] = plaintext
        return plaintext

    def __len__(self) -> int:
        # Convenience for tests asserting how many distinct connectors were
        # actually decrypted (i.e. that repeats across a batch hit the cache).
        return len(self._cache)
