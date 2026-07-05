-- State 1: CollectCustomerIDs (Athena CTAS)
--
-- Builds the temp table the whole backfill run walks through. It selects
-- DISTINCT (tenant_id, customer_id) pairs from all 4 historical source
-- tables, excludes known test tenants, and assigns a 0-indexed row number
-- per tenant partition.
--
-- Row-number contract: ROW_NUMBER() is 1-based in Presto/Athena, so we
-- subtract 1 to get 0-indexed rows. This must stay in lockstep with the
-- backfill-iteration-utility Lambda, which expects offsets starting at 0
-- and treats max_row as an exclusive row COUNT, not the highest index.
-- See mci-backfill/src/backfill/handlers/iteration_utility.py for the
-- Lambda side of this contract.
--
-- Placeholders (substituted by the Step Function / calling code):
--   {database}           Athena/Glue database containing the 4 source tables
--   {temp_table}          fully qualified name for this run's temp table,
--                         e.g. {database}.backfill_temp_{execution_id}
--   {excluded_tenant_ids} comma-separated, single-quoted test/internal tenant
--                         ids to exclude, e.g. 'tenant-test','tenant-internal'
--
-- No explicit output location: this table's data lands under the
-- backfill-workgroup's own configured output location (see
-- terraform/athena.tf). An earlier version of this query set an explicit
-- `external_location`, which Athena rejects outright when
-- enforce_workgroup_configuration is true on the workgroup (confirmed during
-- the live run: "submitted with an 'external_location' property to an Athena
-- Workgroup that enforces a centralized output location"). Rather than
-- weaken that enforced setting, the query defers to it.

CREATE TABLE {temp_table}
WITH (
    format = 'PARQUET',
    write_compression = 'SNAPPY'
) AS
WITH all_customer_ids AS (
    SELECT DISTINCT tenant_id, customer_id FROM action_events
    UNION
    SELECT DISTINCT tenant_id, customer_id FROM audience_membership
    UNION
    SELECT DISTINCT tenant_id, customer_id FROM ingested_attributes
    UNION
    SELECT DISTINCT tenant_id, customer_id FROM transactions
)
SELECT
    tenant_id,
    customer_id,
    ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY customer_id) - 1 AS row_num
FROM all_customer_ids
WHERE tenant_id NOT IN ({excluded_tenant_ids});
