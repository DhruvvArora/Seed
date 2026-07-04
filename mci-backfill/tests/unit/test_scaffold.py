"""Scaffold tests: prove the package imports and, crucially, that the path
dependency on mci-core resolves so backfill can call MCI.

The real handler tests (iterator ranges, day-0 mapping, idempotency) replace
these as each Lambda is implemented.
"""

from __future__ import annotations


def test_backfill_package_imports():
    import backfill  # noqa: F401
    import backfill.handlers.backfill_mci  # noqa: F401
    import backfill.handlers.iteration_utility  # noqa: F401
    import backfill.handlers.list_tenants  # noqa: F401


def test_mci_core_is_reachable_via_path_dependency():
    # This is the whole point of the path dependency: backfill can import and
    # call into mci-core. If the editable install of ../mci-core is missing,
    # this import fails and the scaffold is not wired correctly.
    from mci.invoker.client import resolve_internal_ids
    from mci.model.types import CustomerKey

    key = CustomerKey("tenant-1", "ext-1", internal_customer_id="ext-1")
    assert key.internal_customer_id == "ext-1"
    assert callable(resolve_internal_ids)
