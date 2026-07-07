"""JWT OAuth delivery.

Placeholder for commit 5 (Delivery layer). Will define:
  - build_and_sign_jwt(destination) -> str (RS256 via PyJWT, 1 hour expiry)
  - exchange_for_access_token(destination, signed_jwt) -> str
  - deliver_oauth(destination, payload) -> DeliveryResult
  - Access token cached in memory for the destination, keyed by connector,
    scoped for the lifetime of the invocation, refreshed once and retried
    on a 401 response
"""
