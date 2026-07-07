# Outbound Connector Engine (Project 5)

Fan out and delivery layer. Consumes the plague Kinesis stream (offer
progress events: activated, achieved, completed), looks up which connectors
a tenant has configured, applies an optional JQ transformation, and delivers
to the configured destination: webhook, cross-account Kinesis, or a JWT
OAuth protected endpoint.

This is the Python implementation of Project 5. The original spec was
written against Go; the platform standard is Python 3.12, so all Lambda
handlers, models, and delivery logic here are Python.

## Why no mci-core dependency

Unlike mci-backfill, event-ingress, and audience-ingress, this package does
not call MCI. By the time an event reaches the plague stream it has already
been identity-resolved upstream (by the offer state machine, which sits
after MCI in the pipeline), so aqueduct-distributor works entirely in terms
of internal customer UUIDs already present on the event.

## The jq dependency and its custom layer

The JQ transformation step uses the `jq` PyPI package (real libjq C
bindings), not a pure-Python JSON transform library, to match the spec's JQ
semantics exactly. AWS has no managed layer for it (unlike AWSSDKPandas for
pyarrow), so it ships as a self-built Lambda layer.

Verified before building: the `jq` package's manylinux wheel for cp312
x86_64 statically links libjq and oniguruma (confirmed via `ldd` on the
compiled extension, which showed only standard glibc dependencies). That
means the layer is just `pip install jq --target python/` zipped up, no
from-source manylinux Docker build required. The layer ARN is stored as a
Terraform variable (see `terraform/variables.tf`), the same pattern used for
the AWSSDKPandas layer in audience-ingress.

**This layer needs to be rebuilt and republished whenever the `jq` package
version changes, or whenever the Lambda runtime moves off cp312/Amazon
Linux 2023.**

## Layout

```
src/outbound_egress/
  handlers/
    aqueduct_distributor.py   # plague stream consumer, fan out + deliver
    aqueduct_writer.py        # connector CRUD, invoked by GraphQL layer
    aqueduct_reader.py        # internal connector config loader
  model/                      # Connector, Destination, BatchProgress, etc.
  store/                      # DynamoDB access for connectors, transformations
  crypto/                     # KMS encrypt/decrypt for destination configs
  transform/                  # JQ transformation application
  progress/                   # BatchProgress parsing
  deliver/                    # webhook, kinesis, oauth delivery implementations
tests/unit/                   # scaffold now; per-layer tests as they land
```

## Checks (what CI runs)

```bash
pip install -e ".[dev]"
ruff check src tests
ruff format --check src tests
mypy src
pytest -m "not integration"
```

Terraform (plague stream, connectors/transformations DynamoDB tables, three
Lambdas, custom jq layer, KMS key, IAM) comes as the pipeline is built out.
