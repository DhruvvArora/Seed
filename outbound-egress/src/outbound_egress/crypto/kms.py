"""KMS encrypt/decrypt for the connector destination field.

Placeholder for commit 3 (Crypto layer). Will define:
  - encrypt_destination(kms_key_id, plaintext_json) -> str (base64 ciphertext)
  - decrypt_destination(ciphertext_b64) -> dict

IMPORTANT (decided in chat, do not lose this): the decrypt cache is NOT
built into this module. It must be a plain dict created fresh inside the
aqueduct-distributor handler function body, keyed by
f"{tenant_id}#{connector_name}", and passed down to decrypt_destination
call sites for the duration of a single invocation only.

It must NOT be a module-level dict, because Lambda reuses warm containers
across invocations, and a module-level cache would risk serving a stale
decrypted destination after aqueduct-writer updates a connector's config.
Correctness over the cross-invocation warm-start savings.
"""
