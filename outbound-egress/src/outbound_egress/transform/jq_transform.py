"""Apply a JQ script to an offer progress payload.

The transformation is optional: per the spec, if no transformation_name is
set on a connector, the raw payload is delivered as-is. When one is set, the
connector's transformation_name is used to load a Transformation row (jq
script) from the transformations table, and that script reshapes the
progress payload into whatever format the external system expects (e.g. an
mParticle S2S event object).

The jq package (real libjq C bindings, shipped via the custom Lambda layer,
see terraform/variables.tf) is imported LAZILY inside apply_transformation,
not at module scope. That keeps this module importable, and every other
module that imports it importable, on a machine that has not installed the
jq wheel, which matters for aqueduct-distributor's other code paths and for
any test that does not touch this function.

Malformed scripts: jq.compile() raises ValueError on a syntax error, at
compile time, before any input is processed. Runtime errors (a type
mismatch, or the script's own `error(...)` builtin) also surface as
ValueError. A script that produces no output at all (e.g. `empty`) raises
StopIteration from .first(). All three are caught here and re-raised as
TransformationError, so a malformed or misbehaving script never propagates
an unhandled exception into the distributor's batch loop; it is always a
level playing field, one caught error, and the caller decides whether to
skip this connector or fail the event.
"""

from __future__ import annotations

from typing import Any


class TransformationError(Exception):
    """Raised when a jq script fails to compile or fails to produce a valid
    JSON object at runtime. Never lets the underlying jq exception (or a
    silent bad result) propagate unchecked."""


def apply_transformation(jq_script: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply jq_script to payload. Returns payload unchanged if jq_script is
    None or empty (no transformation configured for this connector)."""
    if not jq_script:
        return payload

    import jq  # lazy import; only present when the custom Lambda layer is attached

    try:
        program = jq.compile(jq_script)
        result = program.input_value(payload).first()
    except (ValueError, StopIteration) as exc:
        raise TransformationError(f"jq transformation failed: {exc}") from exc

    if not isinstance(result, dict):
        raise TransformationError(
            f"jq transformation must produce a JSON object, got {type(result).__name__}"
        )
    return result
