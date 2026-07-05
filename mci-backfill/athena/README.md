# Athena Queries (Project 2 Backfill)

Four SQL queries drive the backfill Step Function. They are Presto/Trino dialect
(Athena's query engine), not standard ANSI SQL, and are not run directly. The
Step Function's Athena SDK integration substitutes the `{placeholder}` values
and executes each one via `startQueryExecution`.

| File | Step Function state | Purpose |
|---|---|---|
| `01_ctas_collect_customer_ids.sql` | CollectCustomerIDs | Dedup all 4 source tables into one temp table, 0-indexed row numbers per tenant |
| `02_get_min_max_row_number.sql` | GetMinMaxRowNumber | Row count for one tenant, to seed the iterator loop |
| `03_get_customer_ids_batch.sql` | GetCustomerIDs | One batch of customer_ids for the current [start, end] range |
| `04_drop_temp_table.sql` | DropTempTable | Cleanup, run at the end (and on failure, via a Catch) |

## The one trap: row-number indexing

`ROW_NUMBER()` in Presto/Trino is 1-based, but the `backfill-iteration-utility`
Lambda (`mci-backfill/src/backfill/handlers/iteration_utility.py`) is proven
against the spec's own named tests to expect 0-indexed offsets, with `max_row`
treated as an exclusive row COUNT rather than the highest row number.

So query 1 subtracts 1 from `ROW_NUMBER()` to make rows 0-indexed, and query 2
returns `row_count` (a `COUNT(*)`) alongside `max_row_num` (the highest actual
index). When the Step Function's `ConfigureCount` state sets `max_row` for the
iterator loop, it must use `row_count`, not `max_row_num`. Wiring the wrong one
in silently drops the last batch (off by one) rather than erroring, so this is
worth double-checking when the ASL is written.

## Validation

These queries were checked for correctness against a synthetic dataset using
DuckDB (same window-function semantics as Presto for this logic), confirming:
cross-table dedup, per-tenant row partitioning starting at 0, and that a batch
range query returns the correct slice. DuckDB does not support Athena's
`CREATE TABLE ... WITH (external_location = ...)` syntax, so that part of query
1 is unverified until the live Athena run; the dedup and numbering logic inside
it is what was proven.
