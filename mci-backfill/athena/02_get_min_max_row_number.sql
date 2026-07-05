-- State: GetMinMaxRowNumber
--
-- Reports how many rows this tenant has in the temp table, so the Step
-- Function knows the starting offset and where to stop.
--
-- Row-number contract: with 0-indexed rows, min_row is always 0 for any
-- tenant with at least one row, and count == max_row_index + 1. The
-- iteration-utility Lambda's max_row parameter is this COUNT (an exclusive
-- upper bound), not the max row_num value itself. Pass row_count, not
-- max_row_num, into the Step Function's max_row field.
--
-- Placeholders:
--   {temp_table}  the CTAS temp table from state 1
--   {tenant_id}   the tenant being processed in this Map iteration

SELECT
    MIN(row_num) AS min_row,
    MAX(row_num) AS max_row_num,
    COUNT(*)     AS row_count
FROM {temp_table}
WHERE tenant_id = '{tenant_id}';
