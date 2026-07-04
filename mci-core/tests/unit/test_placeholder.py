"""Placeholder so `pytest` runs green on a fresh checkout.

Real tests from the spec come next, e.g.:
  test_get_internal_customer_ids_new_mapping
  test_get_internal_customer_ids_existing_mapping
  test_get_internal_customer_ids_race_condition
  test_get_internal_customer_ids_read_only
  test_get_internal_customer_ids_deduplication
  test_forget_external_customer_ids_writes_audit_log
"""


def test_scaffold_imports():
    import mci  # noqa: F401
    import mci.handlers.get_internal_customer_ids  # noqa: F401
    import mci.parallel.pool  # noqa: F401
    import mci.store.dynamodb.table  # noqa: F401

    assert True
