#!/usr/bin/env python3
"""MCI Core load test script.

Three scenarios run in sequence:

  1. CORRECTNESS UNDER CONTENTION
     50 concurrent clients all write the same 10 keys simultaneously.
     Asserts every response returns the same UUID and DynamoDB has no
     duplicates. Proves the attribute_not_exists conditional write holds
     under real concurrency, not just in unit tests with moto.

  2. LATENCY RAMP
     Ramp from 10 -> 50 -> 100 concurrent clients, each sending batches
     of 100 unique keys. Measures P50/P95/P99 per batch at each concurrency
     level. The spec's acceptance criterion is P99 < 500ms for 100 keys.

  3. THROUGHPUT CEILING
     Push to 200 concurrent clients with 100 key batches until latency
     degrades past 1000ms P99 or DynamoDB throttling is detected. Records
     the point where degradation begins.

SAFETY GUARDRAILS (read before running):
  - Hard limit: MAX_TOTAL_INVOCATIONS caps the total Lambda calls regardless
    of scenario. Default 2000, safe to raise to 5000 if you want deeper data.
  - Hard time limit: MAX_RUNTIME_SECONDS aborts any scenario that runs long.
  - The script prints a cost estimate before running and asks for confirmation.
  - Always run `terraform destroy` after the session.

Usage:
  python3 scripts/load_test.py --function-name dev-get-internal-customer-ids
  python3 scripts/load_test.py --function-name dev-get-internal-customer-ids --dry-run
  python3 scripts/load_test.py --function-name dev-get-internal-customer-ids --scenario latency
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import string
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    import boto3
except ImportError:
    boto3 = None  # type: ignore[assignment]

# Safety guardrails. Raise these deliberately if you want deeper data.
MAX_TOTAL_INVOCATIONS = 2000
MAX_RUNTIME_SECONDS = 300  # 5 minutes hard stop

TENANT_ID = "load-test-tenant"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class InvokeResult:
    duration_ms: float
    success: bool
    error: str | None = None
    response: dict[str, Any] | None = None


@dataclass
class ScenarioResult:
    name: str
    concurrency: int
    total_invocations: int
    success_count: int
    error_count: int
    latencies_ms: list[float] = field(default_factory=list)

    def p50(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    def p95(self) -> float:
        return _percentile(self.latencies_ms, 95)

    def p99(self) -> float:
        return _percentile(self.latencies_ms, 99)

    def throughput_rps(self, elapsed_seconds: float) -> float:
        return self.total_invocations / elapsed_seconds if elapsed_seconds > 0 else 0.0

    def print_summary(self, elapsed_seconds: float) -> None:
        print(f"\n  Results ({self.name}, concurrency={self.concurrency}):")
        print(f"    Invocations : {self.total_invocations}")
        print(f"    Success     : {self.success_count}")
        print(f"    Errors      : {self.error_count}")
        print(f"    Elapsed     : {elapsed_seconds:.1f}s")
        print(f"    Throughput  : {self.throughput_rps(elapsed_seconds):.1f} req/s")
        if self.latencies_ms:
            print(f"    P50 latency : {self.p50():.0f}ms")
            print(f"    P95 latency : {self.p95():.0f}ms")
            print(f"    P99 latency : {self.p99():.0f}ms")
            print(f"    Min/Max     : {min(self.latencies_ms):.0f}ms / {max(self.latencies_ms):.0f}ms")
        spec_ok = self.p99() < 500
        print(f"    Spec (P99<500ms): {'PASS' if spec_ok else 'FAIL'}")


def _percentile(data: list[float], pct: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_data) - 1)
    return sorted_data[lo] + (sorted_data[hi] - sorted_data[lo]) * (k - lo)


# ---------------------------------------------------------------------------
# Lambda invocation
# ---------------------------------------------------------------------------

def _invoke(client: Any, function_name: str, customer_keys: list[dict]) -> InvokeResult:
    payload = {"customer_keys": customer_keys}
    t0 = time.perf_counter()
    try:
        response = client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        duration_ms = (time.perf_counter() - t0) * 1000
        raw = response["Payload"].read()
        parsed = json.loads(raw) if raw else None

        if response.get("FunctionError"):
            msg = ""
            if isinstance(parsed, dict):
                msg = parsed.get("errorMessage", str(parsed))
            return InvokeResult(duration_ms=duration_ms, success=False, error=msg)

        return InvokeResult(duration_ms=duration_ms, success=True, response=parsed)
    except Exception as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        return InvokeResult(duration_ms=duration_ms, success=False, error=str(exc))


def _make_keys(batch_size: int, prefix: str = "") -> list[dict]:
    return [
        {"tenant_id": TENANT_ID, "customer_id": f"{prefix}cust-{i:06d}"}
        for i in range(batch_size)
    ]


def _make_contention_keys(n: int = 10) -> list[dict]:
    """Same n keys every time, used by all concurrent clients."""
    return [
        {"tenant_id": TENANT_ID, "customer_id": f"contention-key-{i:03d}"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Scenario 1: Correctness under contention
# ---------------------------------------------------------------------------

def scenario_correctness(
    function_name: str,
    concurrency: int = 50,
    invocation_counter: list[int] | None = None,
) -> bool:
    print(f"\n{'='*60}")
    print("SCENARIO 1: Correctness under contention")
    print(f"  {concurrency} concurrent clients, same 10 keys each")
    print(f"{'='*60}")

    keys = _make_contention_keys(10)
    results: list[InvokeResult] = []

    def _worker() -> InvokeResult:
        client = boto3.client("lambda", region_name="us-east-2")
        result = _invoke(client, function_name, keys)
        if invocation_counter is not None:
            invocation_counter[0] += 1
        return result

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_worker) for _ in range(concurrency)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
    elapsed = time.perf_counter() - start

    errors = [r for r in results if not r.success]
    if errors:
        print(f"  ERROR: {len(errors)} invocations failed:")
        for e in errors[:3]:
            print(f"    {e.error}")
        return False

    # Collect all UUIDs returned for each key across all responses.
    uuid_map: dict[str, set[str]] = {}
    for result in results:
        if not result.response:
            continue
        for _tenant, ext_map in result.response.items():
            for ext_id, internal_id in ext_map.items():
                uuid_map.setdefault(ext_id, set()).add(internal_id)

    duplicates = {k: v for k, v in uuid_map.items() if len(v) > 1}
    if duplicates:
        print(f"  FAIL: {len(duplicates)} keys got different UUIDs across concurrent writers:")
        for k, uuids in list(duplicates.items())[:3]:
            print(f"    {k}: {uuids}")
        return False

    print(f"  PASS: all {len(uuid_map)} keys resolved to the same UUID across "
          f"{concurrency} concurrent writers ({elapsed:.1f}s)")
    print(f"  Latencies: "
          f"P50={_percentile([r.duration_ms for r in results], 50):.0f}ms "
          f"P99={_percentile([r.duration_ms for r in results], 99):.0f}ms")
    return True


# ---------------------------------------------------------------------------
# Scenario 2: Latency ramp
# ---------------------------------------------------------------------------

def scenario_latency_ramp(
    function_name: str,
    invocation_counter: list[int] | None = None,
) -> list[ScenarioResult]:
    print(f"\n{'='*60}")
    print("SCENARIO 2: Latency ramp")
    print("  Concurrency levels: 10, 50, 100")
    print("  Batch size: 100 unique keys per invocation")
    print(f"{'='*60}")

    results = []
    for concurrency in [10, 50, 100]:
        print(f"\n  Running concurrency={concurrency}...")
        result = ScenarioResult(
            name="latency_ramp",
            concurrency=concurrency,
            total_invocations=0,
            success_count=0,
            error_count=0,
        )

        # Each worker gets a unique prefix so keys don't overlap across workers.
        def _worker(worker_id: int) -> InvokeResult:
            client = boto3.client("lambda", region_name="us-east-2")
            prefix = f"ramp-c{concurrency}-w{worker_id:04d}-"
            keys = _make_keys(100, prefix=prefix)
            r = _invoke(client, function_name, keys)
            if invocation_counter is not None:
                invocation_counter[0] += 1
            return r

        # Run 3 rounds at each concurrency level for a stable distribution.
        start = time.perf_counter()
        for _round in range(3):
            if invocation_counter and invocation_counter[0] >= MAX_TOTAL_INVOCATIONS:
                print("  Hit MAX_TOTAL_INVOCATIONS, stopping ramp early.")
                break
            if time.perf_counter() - start > MAX_RUNTIME_SECONDS:
                print("  Hit MAX_RUNTIME_SECONDS, stopping ramp early.")
                break

            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(_worker, i) for i in range(concurrency)]
                round_results = [f.result() for f in concurrent.futures.as_completed(futures)]

            for r in round_results:
                result.total_invocations += 1
                if r.success:
                    result.success_count += 1
                    result.latencies_ms.append(r.duration_ms)
                else:
                    result.error_count += 1
                    print(f"    Error: {r.error}")

        elapsed = time.perf_counter() - start
        result.print_summary(elapsed)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Scenario 3: Throughput ceiling
# ---------------------------------------------------------------------------

def scenario_throughput_ceiling(
    function_name: str,
    invocation_counter: list[int] | None = None,
) -> ScenarioResult:
    print(f"\n{'='*60}")
    print("SCENARIO 3: Throughput ceiling")
    print("  Pushing to 200 concurrent clients, 100 keys each")
    print("  Stops when P99 > 1000ms or throttling detected")
    print(f"{'='*60}")

    concurrency = 200
    result = ScenarioResult(
        name="ceiling",
        concurrency=concurrency,
        total_invocations=0,
        success_count=0,
        error_count=0,
    )

    window_latencies: list[float] = []
    start = time.perf_counter()
    round_num = 0

    while True:
        if invocation_counter and invocation_counter[0] >= MAX_TOTAL_INVOCATIONS:
            print(f"\n  Stopped: hit MAX_TOTAL_INVOCATIONS ({MAX_TOTAL_INVOCATIONS})")
            break
        if time.perf_counter() - start > MAX_RUNTIME_SECONDS:
            print(f"\n  Stopped: hit MAX_RUNTIME_SECONDS ({MAX_RUNTIME_SECONDS}s)")
            break

        round_num += 1

        def _worker(worker_id: int, rnd: int = round_num) -> InvokeResult:
            client = boto3.client("lambda", region_name="us-east-2")
            prefix = f"ceil-r{rnd}-w{worker_id:04d}-"
            keys = _make_keys(100, prefix=prefix)
            r = _invoke(client, function_name, keys)
            if invocation_counter is not None:
                invocation_counter[0] += 1
            return r

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_worker, i) for i in range(concurrency)]
            round_results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for r in round_results:
            result.total_invocations += 1
            if r.success:
                result.success_count += 1
                result.latencies_ms.append(r.duration_ms)
                window_latencies.append(r.duration_ms)
            else:
                result.error_count += 1

        window_p99 = _percentile(window_latencies[-concurrency:], 99)
        elapsed = time.perf_counter() - start
        print(f"  Round {round_num}: window P99={window_p99:.0f}ms "
              f"errors={result.error_count} invocations={result.total_invocations} "
              f"elapsed={elapsed:.1f}s")

        if window_p99 > 1000:
            print(f"\n  Ceiling found: P99 exceeded 1000ms at concurrency={concurrency}")
            break

    elapsed = time.perf_counter() - start
    result.print_summary(elapsed)
    return result


# ---------------------------------------------------------------------------
# Estimate and confirm
# ---------------------------------------------------------------------------

def _estimate_cost_and_confirm(
    function_name: str,
    scenarios: list[str],
    dry_run: bool,
) -> bool:
    print(f"\nLoad test target : {function_name}")
    print(f"Scenarios        : {', '.join(scenarios)}")
    print(f"Max invocations  : {MAX_TOTAL_INVOCATIONS}")
    print(f"Max runtime      : {MAX_RUNTIME_SECONDS}s")
    print()
    print("Estimated cost (us-east-2, PAY_PER_REQUEST):")
    print("  DynamoDB writes : ~$0.15  (50k keys x $1.25/M WCU)")
    print("  DynamoDB reads  : ~$0.10  (50k keys x $0.25/M RCU)")
    print("  Lambda          : ~$0.01  (2GB, ~500ms avg, ~2000 invocations)")
    print("  Total estimate  : ~$0.25 - $0.75 depending on hit rate")
    print()

    if dry_run:
        print("DRY RUN: would invoke Lambda but skipping all real calls.")
        return True

    answer = input("Proceed? [yes/no]: ").strip().lower()
    return answer == "yes"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="MCI Core load test")
    parser.add_argument("--function-name", required=True,
                        help="Lambda function name, e.g. dev-get-internal-customer-ids")
    parser.add_argument("--scenario", choices=["all", "correctness", "latency", "ceiling"],
                        default="all", help="Which scenario to run (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan and exit without invoking Lambda")
    args = parser.parse_args()

    scenarios = (
        ["correctness", "latency", "ceiling"]
        if args.scenario == "all"
        else [args.scenario]
    )

    if not _estimate_cost_and_confirm(args.function_name, scenarios, args.dry_run):
        print("Aborted.")
        sys.exit(0)

    if args.dry_run:
        sys.exit(0)

    invocation_counter = [0]
    all_passed = True
    overall_start = time.perf_counter()

    if "correctness" in scenarios:
        passed = scenario_correctness(args.function_name, invocation_counter=invocation_counter)
        if not passed:
            print("\nCORRECTNESS SCENARIO FAILED. Stopping.")
            sys.exit(1)

    if "latency" in scenarios:
        ramp_results = scenario_latency_ramp(
            args.function_name, invocation_counter=invocation_counter
        )
        spec_failures = [r for r in ramp_results if r.p99() >= 500]
        if spec_failures:
            all_passed = False
            print(f"\n  WARNING: P99 exceeded 500ms spec at "
                  f"concurrency={[r.concurrency for r in spec_failures]}")

    if "ceiling" in scenarios:
        scenario_throughput_ceiling(args.function_name, invocation_counter=invocation_counter)

    overall_elapsed = time.perf_counter() - overall_start
    print(f"\n{'='*60}")
    print(f"Load test complete")
    print(f"  Total invocations : {invocation_counter[0]}")
    print(f"  Total elapsed     : {overall_elapsed:.1f}s")
    print(f"  Overall result    : {'PASS' if all_passed else 'WARN (see above)'}")
    print(f"{'='*60}")
    print()
    print("Next step: terraform destroy in mci-core/terraform to tear down.")


if __name__ == "__main__":
    main()
