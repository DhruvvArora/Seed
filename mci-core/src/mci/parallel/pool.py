"""Thread-pool helper that drives N-way parallel DynamoDB calls.

This is the Python equivalent of the Go `internal/parallel` package. The
project spec calls for 20x concurrency (DYNAMO_PARALLELIZATION_FACTOR).
DynamoDB calls are network-bound (we spend the time waiting on the wire,
not burning CPU), so threads are the right tool: 20 threads each handle a
slice of the work concurrently.

TODO (next step): implement run_parallel(items, fn, workers).
"""
