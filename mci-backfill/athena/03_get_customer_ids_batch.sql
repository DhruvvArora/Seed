-- State: GetCustomerIDs
--
-- Returns one batch (up to 10,000) of customer_ids for this tenant, using the
-- [start, end] range the iteration-utility Lambda computed. Both bounds are
-- inclusive and 0-indexed, matching row_num from state 1's CTAS.
--
-- Placeholders:
--   {temp_table}  the CTAS temp table from state 1
--   {tenant_id}   the tenant being processed in this Map iteration
--   {start}       inclusive lower bound (iteration-utility's "start")
--   {end}         inclusive upper bound (iteration-utility's "end")

SELECT ARRAY_AGG(customer_id) AS customer_ids
FROM {temp_table}
WHERE tenant_id = '{tenant_id}'
  AND row_num BETWEEN {start} AND {end};
