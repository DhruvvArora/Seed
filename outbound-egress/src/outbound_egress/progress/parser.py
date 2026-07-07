"""Parse and validate BatchProgress Kinesis records.

Placeholder for commit 6 (aqueduct-distributor handler). Will define:
  - parse_batch_progress(record_data) -> BatchProgress
  - Each Progress entry's connectors list tells the distributor exactly
    which connector rows to load and invoke for that event.
"""
