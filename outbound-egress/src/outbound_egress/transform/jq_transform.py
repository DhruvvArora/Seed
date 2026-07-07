"""Apply a JQ script to a progress payload.

Placeholder for commit 4 (Transform layer). Will define:
  - apply_transformation(jq_script, payload) -> dict
  - Malformed script and no-transformation-configured paths handled
    explicitly per the spec: if no transformation_name is set on the
    connector, the raw payload is delivered as-is.

The jq package (real libjq C bindings, via a self-built Lambda layer, see
terraform/variables.tf) is imported LAZILY inside functions, not at module
scope, so this package still imports and tests still collect on a machine
that has not installed the layer's wheel locally.

Verified locally (see chat): the jq PyPI package ships a self-contained
manylinux wheel for cp312 x86_64 with libjq and oniguruma statically linked.
`ldd` on the compiled extension shows only standard glibc dependencies, so a
plain `pip install jq --target python/` produces a working layer; no
from-source manylinux Docker build was required, unlike a typical custom
layer build.
"""
