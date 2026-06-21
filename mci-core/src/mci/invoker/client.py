"""Client library other services import to call MCI via Lambda Invoke.

Callers never hard-code the ARN; they pass function name + alias (LIVE /
CANARY) from env vars. This is the importable equivalent of the Go
`api/invoker` package, reused by Projects 2, 3, 4.

TODO: implement resolve_internal_ids(function_name, alias, customer_keys).
"""
