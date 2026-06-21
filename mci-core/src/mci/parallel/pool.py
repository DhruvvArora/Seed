"""Thread-pool helper that drives N-way parallel DynamoDB calls.

Python equivalent of the Go `internal/parallel` package. The spec calls for
20x concurrency (the DYNAMO_PARALLELIZATION_FACTOR env var). DynamoDB calls
are network-bound, so we spend almost all the time waiting on the wire rather
than using the CPU. For that kind of work, plain threads are the right tool:
the GIL is released while a thread waits on I/O, so 20 threads genuinely do
20 requests' worth of waiting at once.

This module stays deliberately small and generic: hand it a list of items and
a function, get back the results. The MCI-specific logic lives in the store
layer, not here, so this can be reused by Projects 3, 4, and 5.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")  # input item type
R = TypeVar("R")  # result type

DEFAULT_WORKERS = 20


def run_parallel(
    items: Iterable[T],
    fn: Callable[[T], R],
    workers: int = DEFAULT_WORKERS,
) -> list[R]:
    """Apply `fn` to every item using up to `workers` threads.

    Results are returned in the SAME ORDER as the input items, regardless of
    which thread finishes first. This ordering guarantee matters: callers map
    results back to inputs positionally.

    If `fn` raises for any item, the exception propagates (fail-fast). The spec
    requires MCI never return a partial result, so surfacing the first error is
    the correct behavior; the caller turns it into an error response.
    """
    item_list: Sequence[T] = list(items)
    if not item_list:
        return []

    # Never spin up more threads than there are items.
    effective_workers = max(1, min(workers, len(item_list)))

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        # executor.map preserves input order and re-raises the first exception
        # when results are consumed.
        return list(executor.map(fn, item_list))
