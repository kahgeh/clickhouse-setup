# Observability stack: OTLP → OpenData Buffer → ClickHouse ← HyperDX

Logs, metrics and traces, all queued through OpenData Buffer (object storage as a durable queue,
no Kafka), stored in ClickHouse, explored in HyperDX. Runs locally with Podman/Docker Compose and
moves to AWS by changing configuration only (see "AWS mapping").

```
web (axum) ──gRPC──► backend (tonic)            test application (../app)
   │ OTLP                  │ OTLP
   └──────────► otel-collector ◄───────┘        ../collector  (patched OpenData exporter)
                  │  one pipeline + one Buffer manifest per signal
                  ▼
             S3 (RustFS locally)                durable queue
                  │ pull
                  ▼
   ingestor-logs / -traces / -metrics           ../ingest  (one process per signal)
                  │ idempotent inserts
                  ▼
              ClickHouse  ◄──── HyperDX (UI :8080, API :8000)
```

## Run it

```sh
# 1. build the three images once (arm64 or amd64 host; see "Images")
docker build -t sre-app:dev                       ../app
docker build -f ../collector/otel-collector/Dockerfile -t sre-collector:dev ../collector
docker build -f ../ingest/connectors/clickhouse-ingestor/Dockerfile -t sre-ingestor:dev ../ingest

# 2. start the stack and the test app, then provision HyperDX (from the repository root: `make setup`)
docker compose --profile app up -d
python3 hyperdx/provision.py            # connection, sources, dashboards, saved searches (idempotent)

# 3. prove it works (exit code non-zero on any failure)
python3 verify/verify.py                # load + 32 checks against ground truth
python3 verify/selftest.py              # negative tests: tampered data must be detected
python3 verify/chaos.py                 # crash / outage / poison-pill scenarios under load
python3 verify/alerts.py                # HyperDX alerts: quiet, boundary, fire, webhook, resolve (about 6-25 min; HyperDX evaluates once a minute)
```

| What | Where |
|---|---|
| HyperDX UI | http://localhost:8080 (dev login `admin@local.dev` / `LocalDevPass1!`, dev only) |
| Dashboards | *Availability & Latency* and *Runlog* (created by `provision.py`) |
| Alerts | *Alert: 5xx errors above 4 per minute* (dashboard tile) and *Alert: ERROR logs above 4 per minute* (saved search), webhook to `webhook-sink` (:8089, `GET /received`) |
| Error list | saved searches *Error list: error logs* / *failed spans* and a tile on the dashboard |
| Runlog | dashboard *Runlog* (filter by `test_run_id`) or saved searches `Runlog <run id>: logs / spans` |
| ClickHouse | http://localhost:8123 (`ingestor`/`ingestor`, dev only); read-only `hyperdx_ro` for HyperDX |
| Ingestor metrics | :9091 logs, :9092 traces, :9093 metrics (`/metrics`) |
| Test app | web :8081, backend :50051 |

## Lifecycle and reset

From the repository root (`Makefile`; destructive targets ask first, `YES=1` skips the question):

```sh
make help          # list the targets
make start         # start the containers
make stop          # stop them; containers, volumes and data stay
make setup         # start + provision HyperDX: from nothing to ready
make teardown      # remove containers, network, volumes and hyperdx/state.json (IMAGES=1: images too)
make status        # containers (also stopped ones) and the row count of every table
make reset-data    # empty the Buffer bucket, the ClickHouse tables (logs, traces, metrics) and the webhook sink list, restart the pipeline
make reset-setup   # wipe HyperDX's store (Mongo) and re-create dashboards, alerts and saved searches
make reset         # both, in that order
make provision     # re-create the HyperDX content only; idempotent, deletes nothing
```

`setup` expects the three images to exist already (see "Run it"). `DOCKER=podman` switches the container tool, for `make` and for `verify/verify.py`.

The data reset stops the test app, collector, ingestors and webhook sink first, so nothing writes while the bucket and tables are emptied,
and starts them again afterwards. The app's cumulative metric counters restart from zero, and the webhook sink's list of received alerts is emptied.
`reset-data` also works on a stack stopped with `make stop`: it starts the services it needs and waits for them.
The setup reset also removes the HyperDX user, so the next login uses the dev credentials that `provision.py` creates again.

The containers restart on their own unless stopped with `make stop` (`s3-init` is a one-shot job and does not).
After the Podman machine reboots they come back only if its `podman-restart.service` is enabled:
`podman machine ssh systemctl --user enable podman-restart.service` (enabled on this Mac as of 2026-10-17).
The data lives in the named volumes `obs_clickhouse-data`, `obs_mongo-data` and `obs_s3-data` and survives restarts;
only `make teardown`, `make reset` / `reset-data` / `reset-setup` or removing the Podman machine deletes it.

## What "working" means

`verify/verify.py` sends a deterministic request mix and compares what arrived with the
load generator's own ground truth, never with the telemetry being checked:

* **A** pipeline: logs / spans / metric counters / histogram counts equal the requests sent; no duplicates (raw = FINAL = unique provenance keys)
* **B** distributed tracing: every `/chain` trace is exactly web server → web client → backend server → backend internal, one trace id across both services, logs carry that id; errors propagate on `/chain-fail`
* **D** HyperDX: its own search and chart APIs return the same counts, availability, per-route breakdown and `/slow` latency; error lists and runlog searches match; dashboards exist
* **E** end-to-end latency measured on every row (server insert time minus event time), threshold p99 ≤ 15 s. Observed locally: spans p50 ≈ 0.65 s / p99 ≈ 4.5 s, logs p50 ≈ 0.4 s / p99 ≈ 1.3 s (the same on emulated amd64)

`selftest.py` tampers with ClickHouse data (delete / flip / duplicate / re-parent / inflate) and
requires the right check to fail each time. `chaos.py` injects faults while load is flowing:
ingestor SIGKILL and restart, ClickHouse down for 20 s, object store down for 8 s and for 30 s,
and malformed payloads written straight into the queues. The collector is restarted **only while
idle** (a restart under load can duplicate or lose in-flight requests at the app→collector edge).
An insert-token probe shows ClickHouse drops a replayed chunk. Each fault scenario prints how many
replayed inserts ClickHouse actually dropped as duplicates (read from its server log): in three
repeated runs the SIGKILL scenario caused 1, 2 and 2 real replays and the data stayed exact, while
graceful restarts caused none (they flush acknowledgements first). Whether a kill lands mid-commit
is timing-dependent, so a single run can see zero. `chaos.py` and `verify.py` drive the local compose project (docker, `obs-*`
containers, `localadmin`); on AWS they would need adapting.

`alerts.py` checks the alerting path end to end against the HyperDX alerts created by `provision.py`
(both "above 4 per minute", 1-minute interval, generic webhook to the local `webhook-sink`):
healthy traffic leaves both alerts OK and delivers nothing; **exactly 4 failures (the threshold, not
more) must not fire** (this boundary probe is what distinguishes HyperDX's `above`, which means
>=, from `above_exclusive`, which means >; the alerts use the exclusive form); a burst of 60 failed
requests (70 ERROR logs) moves both to ALERT (about 20-50 s observed) and delivers a webhook; the
counts in the firing notifications add up to what was sent when the burst falls in the evaluated
window; once the failures stop both return to OK and a resolve notification arrives. Negative tests
done: unreachable thresholds make the fire checks fail; the old `>=` comparison makes the boundary
probe fail. The alerts stay enabled, so every `verify.py` / `chaos.py` run also fires them; the
webhook sink keeps its list in memory (`DELETE /received` clears it).

## What was patched, and why

* **`../collector` – OpenData exporter (upstream v0.5.0) + two local patches.** Upstream supports
  only logs and metrics, so a **traces signal** was added (signal byte 3, which the ingest envelope
  already defines). Upstream also has **no retry**: a failed object-store write logged
  "Exporting failed. Dropping data" and lost the batch, found by the S3-outage chaos test; a
  configurable `retry_on_failure` (default on) was added.
* **`../ingest` – opendata-contrib @3668618, vendored.** The runtime/plugin crates are not on
  crates.io and `RowValue` is a closed enum (no floats/arrays), so extending it needed the source.
  Added: traces and metrics decoders and ClickHouse adapters, a shared deterministic chunk planner,
  a `signal:` setting, and a generic sink. **Deliberate behaviour change:** a payload that is not
  valid OTLP protobuf is counted (`ingestor_decode_malformed_entries_total{signal}`) and skipped
  instead of failing the pipeline forever on one poison pill; a wrong-signal envelope stays fatal.
  Summary and exponential-histogram metrics are counted in `ingestor_decode_skipped_points_total`
  and not stored (HyperDX cannot chart them).
* **MinIO is gone** (Docker Hub images deleted Sept 2026); **RustFS** is the local S3. Buffer's
  manifest needs S3 conditional writes (compare-and-set), which RustFS and AWS S3 provide.
* **Schema.** `clickhouse/*.sql` mirrors ClickStack's `otel_logs`, `otel_traces`,
  `otel_metrics_{gauge,sum,histogram}` plus `_odb_*` provenance columns, as
  `ReplacingMergeTree` with the provenance key in `ORDER BY` and
  `non_replicated_deduplication_window`, so a replayed insert token is dropped at insert time on a
  single node (proved by `chaos.py dedup-token`) and the merge is the backstop.

## AWS mapping (documented, not yet executed: see "Not verified")

| Local | AWS | Config change only |
|---|---|---|
| RustFS | S3 bucket | Unset `S3_ENDPOINT`/`AWS_ENDPOINT`, `AWS_ALLOW_HTTP`, `AWS_VIRTUAL_HOSTED_STYLE_REQUEST`; credentials from the task/pod IAM role. Role needs `s3:GetObject`, `PutObject`, `DeleteObject` (Buffer GC), `ListBucket` on the bucket. |
| collector container | ECS/EKS, stateless, scale horizontally | `BUFFER_BUCKET`, `AWS_REGION`; same config file |
| `ingestor-*` | ECS/EKS, one deployment per signal | `INGESTOR__CLICKHOUSE__ENDPOINT/USER/PASSWORD`, `INGESTOR__BUFFER__OBJECT_STORE__BUCKET`. Run one replica per signal until multi-consumer behaviour on one manifest is confirmed. |
| ClickHouse container | ClickHouse Cloud, or EC2/EKS | Apply `clickhouse/*.sql` (use `Replicated*` engines on a cluster); create `hyperdx_ro`; secrets from Secrets Manager |
| HyperDX + Mongo | ECS/EKS + a MongoDB | `MONGO_URI`, `FRONTEND_URL`; run `provision.py` with `HDX_UI/HDX_API/CH_*` |
| dev credentials in compose/files | Secrets Manager / SSM | none are used outside local |

All images are plain containers configured by env vars and YAML. Base images are multi-arch.

## Images

`sre-app`, `sre-collector` and `sre-ingestor` build natively on arm64 (what this was developed
on). Their Dockerfiles carry no architecture flags and every base image publishes arm64 and amd64,
so the same builds work for Graviton and x86; the upstream OpenData images are amd64-only, which is
why the collector is built here.

## Not verified (honest limits)

* **Real AWS S3.** Everything was proved against RustFS. The AWS run needs your AWS access and an
  explicit go-ahead. `verify.py` (ClickHouse and HyperDX URLs via `CH_URL`, `HDX_*`) is the part
  meant to be reused; load generation and `chaos.py` are tied to the local compose project.
* **HyperDX in a browser: partly closed.** The Chrome extension never connected, but the dashboard was
  rendered and looked at through headless Chrome driven over the DevTools Protocol with no new
  dependencies (`course/tools/shot.mjs`). That showed a real flaw (empty time buckets drawn as 0%
  availability), now fixed with `fillNulls: false`. The numbers behind each tile are still verified
  through HyperDX's APIs, and only the main dashboard has been inspected visually so far.
* **amd64 images were run only under emulation** on an Apple Silicon host (`compose.amd64.yml`):
  the full verification passed 31/31 on x86_64 processes. They have not run on real x86 hardware.
* The HyperDX external API is rate limited to 100 requests per window; the verifier backs off on 429.
* **HyperDX checks are equivalent queries, not the dashboard tiles.** Counts, availability, per-route
  breakdowns, p50 latency and the saved error-list searches are computed by HyperDX's own APIs; the
  dashboard's tile definitions are validated by HyperDX but not executed (no browser). The
  *app.requests by outcome* tile undercounts because HyperDX drops each cumulative series' first point.
* **Metrics checks use the run's final cumulative value (`max`)**, so a single lost intermediate
  snapshot is not detected (the final value, which carries the total, is). Gauge storage was
  exercised with generated data, not by the test app.
* The verifier uses `1d` chart buckets for traces, so a run that spans UTC midnight is not handled.
* At-least-once at the app→collector edge: an SDK retry after a lost ack can duplicate a request's
  telemetry. Inside the pipeline (Buffer → ClickHouse) duplicates are removed.

* **Alerts do not see late-arriving rows.** HyperDX's in-app task runs at second :00 and evaluates
  the minute just ended exactly once, with no allowance for ingest delay. Rows that reach ClickHouse
  after :00 (the pipeline's span p99 is several seconds) are never counted by an alert, so failures in
  the last seconds of a minute can go unalerted. `alerts.py` starts its bursts early in a minute to
  avoid this; real traffic has no such luxury.

### Durability limits you should know (found in review, not all fixed)

* **A collector crash loses what it holds in memory.** The collector acknowledges the app before
  data is durable in some stages (batch processor, in-memory sending queue, retry loop). Data
  becomes durable once the exporter's write to object storage succeeds.
* **Object-store outages are retried, with limits.** The patched exporter retries for up to its
  `retry_on_failure.max_elapsed_time` (default 5 min). A 30 s outage was survived under load. The
  Buffer producer also has an internal manifest-retry budget; if it were exhausted the signal could
  stall until the collector restarts (watch the `buffer.producer.halted` gauge). Not reproduced
  at 30 s, not tested beyond.
* **Poison pills are only handled for undecodable protobuf.** A malformed *envelope* (wrong or
  unknown signal byte) stops that ingestor on purpose, because it indicates a routing bug. Valid
  OTLP carrying values ClickHouse rejects (for example a timestamp past year 2262) is retried
  forever by the writer and blocks that signal's queue until fixed.
* Dev credentials in compose and scripts are for local use only. `INGESTOR__BUFFER__OBJECT_STORE__REGION`
  overrides the region that is hard-coded in `config/ingestor-*.yaml`.
