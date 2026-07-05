-- State: DropTempTable
--
-- Cleanup, run at the end of the workflow (and on failure via a Catch, per
-- the spec) so the S3 temp data does not linger after the one-time backfill
-- completes.
--
-- Placeholders:
--   {temp_table}  the CTAS temp table from state 1

DROP TABLE IF EXISTS {temp_table};
