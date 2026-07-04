# MCI Backfill Pipeline (Project 2)

One-time migration that walks every historical `(tenant_id, external_customer_id)`
pair across the four Athena source tables and creates its MCI mapping, so the
platform can turn on without losing customer history.

It is an AWS Step Function that fans out over tenants (max 3 concurrent), walks
each tenant's rows in 10,000-row batches, and calls the `backfill-mci` Lambda per
batch. Backfill uses the **day-0 strategy**: `internal_customer_id == external_customer_id`,
so historical records stay queryable under the same key during the transition.

## How it reaches MCI

Backfill does not write to the MCI table directly. It calls the MCI Lambda through
`mci-core`'s invoker client (`mci.invoker.client.resolve_internal_ids`), supplying
the internal id per key so no fresh UUID is minted for historical records. That
keeps a single write path into MCI, shared with Projects 3 and 4.

## Path dependency on mci-core (monorepo)

This package depends on `mci-core`, which lives in the sibling `../mci-core`
folder and is **not** published to PyPI. Install both as editable local packages
in one command, from this folder:

```bash
pip install -e ../mci-core -e ".[dev]"
```

Because `../mci-core` is supplied as a local editable install in the same
command, pip uses it to satisfy this package's `mci-core` dependency and never
tries to fetch it from an index. If you install `.[dev]` on its own first, pip
will fail looking for `mci-core` on PyPI; always include `-e ../mci-core`.

## Checks (what CI runs)

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -m "not integration"
```

## Layout

```
src/backfill/
  handlers/
    backfill_mci.py         # day-0 mapping via mci-core invoker
    iteration_utility.py    # next batch range for the iterator loop
    list_tenants.py         # active tenant ids for the Map state
tests/unit/                 # scaffold now; per-Lambda tests as they land
```

Terraform (Step Function, Lambdas, Athena workgroup, IAM) and the ASL state
machine come as the pipeline is built out.
