# Seed Platform

Python implementation of the Formation-style marketing personalization
platform. Built as five projects, in dependency order.

| # | Project | Folder | Status |
|---|---------|--------|--------|
| 1 | MCI Core Service | `mci-core/` | scaffold |
| 2 | MCI Backfill Pipeline | `mci-backfill/` | not started |
| 3 | Event Ingress Service | `event-ingress/` | not started |
| 4 | Audience Ingress Service | `audience-ingress/` | not started |
| 5 | Outbound Connector Engine | `outbound-egress/` | not started |

Each project is a self-contained Python package with its own `pyproject.toml`,
`src/`, `tests/`, and `terraform/`. Shared code (e.g. the MCI invoker client)
is imported across projects.

See each project folder's README for details. Start with `mci-core/`.
