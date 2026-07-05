-- Creates 4 external tables over the seed CSVs, standing in for the real
-- historical source tables during the live run. Run each of these with
-- `aws athena start-query-execution`, or paste into the Athena console.
--
-- Placeholders to substitute before running:
--   {database}    a throwaway Glue database, e.g. backfill_live_test
--   {s3_prefix}   e.g. s3://{your-throwaway-bucket}/source-data

CREATE EXTERNAL TABLE {database}.action_events (
    tenant_id STRING,
    customer_id STRING
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
LOCATION '{s3_prefix}/action_events/'
TBLPROPERTIES ('skip.header.line.count'='1');

CREATE EXTERNAL TABLE {database}.audience_membership (
    tenant_id STRING,
    customer_id STRING
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
LOCATION '{s3_prefix}/audience_membership/'
TBLPROPERTIES ('skip.header.line.count'='1');

CREATE EXTERNAL TABLE {database}.ingested_attributes (
    tenant_id STRING,
    customer_id STRING
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
LOCATION '{s3_prefix}/ingested_attributes/'
TBLPROPERTIES ('skip.header.line.count'='1');

CREATE EXTERNAL TABLE {database}.transactions (
    tenant_id STRING,
    customer_id STRING
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
LOCATION '{s3_prefix}/transactions/'
TBLPROPERTIES ('skip.header.line.count'='1');
