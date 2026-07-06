"""Lambda entry point: joust (API key authorizer).

Validates the x-api-key header against the apikey-metadata DynamoDB table and
returns an IAM policy that API Gateway uses to allow or deny the request. On
Allow, the resolved tenant_id is injected into the request context so that
event-enqueue can read it without doing its own DynamoDB lookup.

Authorizer type: TOKEN
Identity source: method.request.header.x-api-key

The event shape for a TOKEN authorizer:
  {
    "type": "TOKEN",
    "authorizationToken": "<value of x-api-key header>",
    "methodArn": "arn:aws:execute-api:<region>:<account>:<api-id>/<stage>/<method>/<resource>"
  }

The return shape:
  {
    "principalId": "<api_key>",
    "policyDocument": {
      "Version": "2012-10-17",
      "Statement": [{"Action": "execute-api:Invoke", "Effect": "Allow|Deny", "Resource": "..."}]
    },
    "context": {"tenant_id": "<tenant>"}   # present only on Allow
  }

Caching
-------
Two layers of caching exist:
  1. API Gateway caches the authorizer result by identity token for
     `authorizer_result_ttl_in_seconds` (configured in Terraform). This
     reduces Lambda invocations at the infra level.
  2. The ApiKeyCache below caches DynamoDB lookups in memory within a warm
     Lambda container for `CACHE_TTL_SECONDS` (default 900 = 15 minutes).
     This reduces DynamoDB reads for requests that do reach this Lambda.
"""

from __future__ import annotations

import logging
import os

from event_ingress.auth.api_key_cache import ApiKeyCache

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-scope singleton -- created once per warm container.
API_KEY_TABLE_NAME = os.environ.get("API_KEY_TABLE_NAME", "")
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "900"))

_cache: ApiKeyCache | None = None


def _get_cache() -> ApiKeyCache:
    global _cache
    if _cache is None:
        _cache = ApiKeyCache(
            table_name=API_KEY_TABLE_NAME,
            ttl_seconds=CACHE_TTL_SECONDS,
        )
    return _cache


# ---------------------------------------------------------------------------
# IAM policy helpers
# ---------------------------------------------------------------------------


def _allow_policy(api_key: str, method_arn: str, tenant_id: str) -> dict:
    return {
        "principalId": api_key,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    "Resource": method_arn,
                }
            ],
        },
        # tenant_id is available to downstream Lambdas as
        # event["requestContext"]["authorizer"]["tenant_id"]
        "context": {"tenant_id": tenant_id},
    }


def _deny_policy(api_key: str, method_arn: str) -> dict:
    return {
        "principalId": api_key,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": "Deny",
                    "Resource": method_arn,
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(event: dict, context=None) -> dict:
    """Validate x-api-key and return an Allow or Deny IAM policy."""
    api_key: str = event.get("authorizationToken", "")
    method_arn: str = event.get("methodArn", "*")

    if not api_key:
        logger.warning("joust: missing authorizationToken in event")
        return _deny_policy("", method_arn)

    tenant_id = _get_cache().get_tenant_id(api_key)

    if tenant_id is None:
        logger.info("joust: key not found or disabled, returning Deny")
        return _deny_policy(api_key, method_arn)

    logger.info("joust: key resolved to tenant %s, returning Allow", tenant_id)
    return _allow_policy(api_key, method_arn, tenant_id)
