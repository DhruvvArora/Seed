# MCI Core Service (Project 1)

Master Customer Index. Resolves any external customer ID to a stable internal
UUID. Every event, offer, and audience in the platform depends on this, so it
is built first. Also the CCPA right-to-be-forgotten endpoint lives here.

This is the Python implementation of Project 1. The original spec is written
for Go; the section below maps that layout onto Python so the structure stays
recognizable.

## Layout

```
mci-core/
  pyproject.toml            Project metadata, pinned dev deps, lint/type/test config
  src/mci/
    handlers/               One module per Lambda entry point (Go: cmd/<lambda>/)
      get_internal_customer_ids.py
      get_external_customer_ids.py
      forget_external_customer_ids.py
    store/dynamodb/         DynamoDB key build, BatchGetItem, conditional PutItem
    model/                  Typed request/response/item shapes
    parallel/               Thread-pool for 20x parallel DynamoDB calls
    invoker/                Client library other services import to call MCI
  tests/
    unit/                   Fast tests, AWS mocked with moto
    integration/            Round-trip tests against DynamoDB Local
  terraform/mci-core/       Infra: table, GSI, three Lambdas, IAM
  .github/workflows/ci.yml  Lint, format, type-check, test on every push
```

## Go-to-Python mapping

| Spec (Go)            | Here (Python)                  |
|----------------------|--------------------------------|
| `cmd/<lambda>/`      | `src/mci/handlers/<lambda>.py` |
| `internal/store/...` | `src/mci/store/...`            |
| `internal/parallel/` | `src/mci/parallel/`            |
| `internal/model/`    | `src/mci/model/`               |
| `api/invoker/`       | `src/mci/invoker/`             |
| goroutine pool (20x) | `ThreadPoolExecutor` (20)      |
| AWS SDK for Go       | `boto3` (preinstalled in Lambda runtime) |

`boto3` ships inside the Lambda Python runtime, so MCI Core has no native
dependencies to bundle. The dependency-packaging decision (zip vs layer vs
container) is deferred until a later project needs a compiled library.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -m "not integration"     # should pass on a fresh checkout
ruff check src tests
mypy src
```

## Build order (whole platform)

1. **MCI Core (this repo)** — nothing depends on, everything depends on it
2. MCI Backfill
3. Event Ingress
4. Audience Ingress
5. Outbound Connector Engine

## Status

Scaffold only. Modules contain documented stubs describing what each will hold.
Next step: implement `parallel/pool.py` and `store/dynamodb/table.py` (the
conditional-write + thread-pool core that the rest reuses).
