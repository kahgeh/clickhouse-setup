# clickhouse-setup: an observability platform on ClickHouse

A logs, metrics and traces platform, with ClickHouse as its store, built from OpenTelemetry, OpenData Buffer, ClickHouse and HyperDX. It runs locally with
Docker or Podman Compose for learning and trial, and is designed to move to AWS by changing configuration. It comes with a Rust
test application, a verification suite that proves the data arrives intact, a cost model, and a static web course that explains
the problem, the design and the evidence.

```
web (axum) ──gRPC──► backend (tonic)            test application (app/)
   │ OTLP                  │ OTLP
   └──────────► otel-collector ◄───────┘        collector/  (patched OpenData exporter)
                  │  one pipeline and one Buffer manifest per signal
                  ▼
             S3 (RustFS locally)                durable queue (OpenData Buffer)
                  │ pull
                  ▼
   ingestor-logs / -traces / -metrics           ingest/  (one process per signal)
                  │ idempotent inserts
                  ▼
              ClickHouse  ◄──── HyperDX (UI :8080)
```

The Buffer is object storage used as a durable queue, so there is no Kafka. Each signal has its own queue and ingestor, and the
ingestors insert idempotently, so a retry or a crash does not duplicate rows.

## Quick start

You need Docker or Podman with Compose, Python 3 and `make`. Everything runs on one machine.

```sh
# 1. build the three images once (see stack/README.md for details)
docker build -t sre-app:dev       app
docker build -f collector/otel-collector/Dockerfile -t sre-collector:dev collector
docker build -f ingest/connectors/clickhouse-ingestor/Dockerfile -t sre-ingestor:dev ingest

# 2. start the stack and the test app, and provision HyperDX
make setup

# 3. prove it works
python3 stack/verify/verify.py        # load plus 32 checks against ground truth
```

HyperDX is at http://localhost:8080. Local development login: `admin@local.dev` / `LocalDevPass1!` (dev only). It has two dashboards
(Availability & Latency, Runlog), saved searches for the error lists and runlogs, and two alerts that deliver to a webhook sink.
Send a request to see a trace: `curl -i localhost:8081/checkout` returns an `x-trace-id` header, and searching for that id in the
Traces source shows the waterfall.

## Commands

From the repository root, `make help` lists them. Destructive targets ask first, and `YES=1` skips the question.

| Command | What it does |
|---|---|
| `make start` / `make stop` | Start the containers; stop them (containers, volumes and data stay). |
| `make setup` / `make teardown` | `setup` starts the stack and provisions HyperDX, from nothing to ready. `teardown` removes containers, network, volumes and cached ids (`IMAGES=1` also removes the images). |
| `make reset-data` | Empty the Buffer bucket and the ClickHouse tables (logs, traces, metrics), restart the pipeline. |
| `make reset-setup` | Wipe HyperDX's own store and recreate dashboards, alerts and saved searches. |
| `make reset` | Both of the above. |
| `make provision` | Recreate the HyperDX content only; idempotent, deletes nothing. |
| `make status` | Containers, and the row count of every table. |

These delete local data. They are for the learning stack only.

The proof suite in `stack/verify/`:

| Script | What it proves |
|---|---|
| `verify.py` | 32 checks: what arrived equals what the load generator sent, no duplicates, distributed traces, HyperDX charts, latency. |
| `selftest.py` | Negative tests: 8 deliberate corruptions that the checks must detect. |
| `chaos.py` | Failure injection under load: killed and restarted ingestors, ClickHouse and S3 outages, garbage entries, collector restart, token replay. |
| `alerts.py` | The alerts stay quiet on healthy traffic, fire on failures, deliver the webhook and resolve (6 to 25 minutes). |

## What is where

| Path | Contents |
|---|---|
| `stack/` | Compose files, collector and ingestor configuration, ClickHouse DDL, HyperDX provisioning (`hyperdx/provision.py`), the verification suite and the webhook sink. Its README is the runbook, with the AWS mapping. |
| `app/` | The Rust test application: axum web, tonic backend, a load generator with a ground-truth file, and `trace_macros` (`#[trace_all]`, one attribute that puts a span on every function of a block). |
| `collector/` | Vendored OpenData collector exporter, patched to add traces and retry on failure. |
| `ingest/` | Vendored OpenData ingestors, extended with trace and metric decoders and ClickHouse adapters. |
| `cost/` | The AWS price-list driven cost model and comparison, with the assumptions written down. |
| `course/` | A static HTML course with simulations, quizzes and the captured evidence. Open `course/index.html` in a browser. |
| `tasks/` | Goals, plans, review notes and lessons learned. |

## The course

`course/index.html` opens an 11-module course in three tracks: the story for everyone (about 20 minutes), using it for developers,
and running it for platform engineers. Each module has a figure or an interactive, real captured evidence (screenshots, logs,
payloads at every hop, test reports) and a short quiz. The evidence in `course/evidence/` records the source command and capture
time of every item. See `course/README.md` to regenerate it.

## What has and has not been shown

Proven locally: the data arrives complete and not duplicated, including through crashes and outages; traces carry one id across
services; the dashboards, error lists, runlogs and alerts work; the end-to-end latency is a few seconds at the 99th percentile.

Not shown, and stated in the course (module 11):
- It has not run against real AWS S3.
- It has not been load-tested at production volume.
- A collector crash loses the batches it holds in memory.
- An object-store outage longer than the collector's 5-minute retry window loses data, and outages from 30 seconds to 5 minutes were not tested.
- The cost comparison is a model with assumptions, including the assumed engineer time. It does not support a flat "N times cheaper" claim.
- The ClickHouse Cloud prices in the model are unconfirmed.
- The Go collector modules have not been through a supply-chain audit.

## Licence and sources

The ingestor and collector code under `ingest/` and `collector/` are vendored from OpenData projects and keep their own licences
(see the `LICENSE` files in those directories).
