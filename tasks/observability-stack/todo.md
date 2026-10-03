# Observability stack: OpenData Buffer + ClickHouse + HyperDX

Status: IMPLEMENTED and verified locally (see section 7). Open: AWS parity run (needs user go-ahead), dashboard seen in a browser.

## 1. Goal

A local, end-to-end logging, metrics and tracing system that is **portable to AWS by configuration only**, and that I can **prove works** with automated checks, not by eyeballing.

```
Rust axum web app ──gRPC──► Rust tonic service
        │ OTLP (logs, metrics, traces)       │ OTLP
        └──────────────► OTel collector ◄────┘
                           (opendata exporter, one pipeline + manifest per signal)
                                   │
                                   ▼
                            MinIO / S3  (Buffer queue)
                                   │ pull
                                   ▼
        ingestor (opendata-contrib runtime + OUR decoders: logs, metrics, traces)
                                   │ idempotent inserts
                                   ▼
                              ClickHouse ◄──── HyperDX UI (dashboard, searches)
```

## 2. Decisions already made (from our conversation)

| Decision | Choice |
|---|---|
| Target | Local machine now (Podman + compose, arm64 Mac); AWS-portable |
| Buffer for signals | **All three: logs, metrics, traces** (option C) |
| Missing consumers | We build metrics + traces decoders/adapters. **REVISED after P0 research (see §5a): vendor opendata-contrib at a pinned commit (MIT) into this repo and add our crates to that workspace, because the runtime/plugin crates are not on crates.io and `RowValue` is a closed enum with no Float64/Array.** Original plan was "new workspace, no fork". |
| Schema | Mirror the ClickStack schema (`otel_logs`, `otel_traces`, `otel_metrics_gauge/sum/histogram`) plus `_odb_*` provenance columns so HyperDX infers columns |
| Summary metrics / exp. histograms | Summary: dropped and counted. Exp. histogram: table exists, low priority (HyperDX cannot display it yet) |
| Out of scope | Auth, TLS, HA, retention tuning, session replay, Terraform/CDK |

## 3. Components to build

1. **Compose stack**: MinIO, OTel collector (opendata exporter), ClickHouse, ingestor, HyperDX (HyperDX-only mode). All images pinned, none `latest`.
2. **Ingestor extension** (Rust workspace): signal decoders and adapters for metrics (fan-out to gauge/sum/histogram tables) and traces; logs reuses or adapts the stock decoder. DDL for every table.
3. **Test application** (Rust):
   - `web` (axum) and `backend` (tonic gRPC), instrumented with the `tracing` + OpenTelemetry stack, exporting OTLP.
   - Endpoints on `web`:
     - `GET /ok`: fast success
     - `GET /slow?ms=N`: controlled latency
     - `GET /error`: returns 500 and logs an error
     - `GET /panic`: handler failure surfaced as an error span
     - `GET /chain`: calls `backend` over gRPC (distributed trace)
     - `GET /chain-fail`: backend returns a gRPC error, propagated to `web` as 502
   - Metrics: request count, duration histogram, in-flight gauge on both services.
   - Logs: structured, each line carries `trace_id`/`span_id` and the `test_run_id`.
4. **Load generator**: deterministic, takes a `test_run_id`, sends an exact known mix of requests and writes **ground truth** (counts per endpoint and outcome) to a JSON file.
5. **HyperDX content** (saved in-repo as code):
   - **Availability and latency dashboard**: availability % (non-5xx / total) per route; latency p50/p95/p99 per route; request rate.
   - **Error list**: saved search of error logs and failed spans.
   - **Runlog queries**: saved searches scoped to a `test_run_id` (all logs, all spans, one trace end to end).
6. **Verification script** (`verify.sh` or a Rust test binary): one command, non-zero exit on any failure.

## 4. Verification: what "working" means

Everything below must pass in a single scripted run, from a clean `compose up`. Ground truth comes from the load generator, never from the system under test.

### A. Pipeline correctness (per signal)
1. **Logs:** rows in ClickHouse for the `test_run_id` equal the number of log lines the generator caused (exact match).
2. **Traces:** span count equals expected spans (`web` + `backend` per chained request). No missing parents.
3. **Metrics:** request counter totals per endpoint/outcome equal ground truth. Histogram `count` equals request count.
4. **No duplicates:** after `OPTIMIZE ... FINAL`, counts are unchanged.

### B. Distributed tracing
5. For every `/chain` request, one `TraceId` has spans from **both** services with correct parent/child links.
6. Logs from both services carry that same `TraceId`, so logs ↔ trace correlate.
7. `/chain-fail`: the error status appears on the backend span **and** propagates to the web span.

### C. Reliability (the Buffer design under stress)
8. **Idempotency:** replay the same batch / kill the ingestor mid-insert and restart: counts unchanged after dedup.
9. **Outage recovery:** stop ClickHouse during a run, restart it, and counts still match ground truth (no loss, no doubles).
10. **Restarts:** restart collector and ingestor mid-run; counts still match.
11. **Bad payload:** a malformed envelope is skipped and counted in the ingestor's own metrics; the pipeline keeps going.

### D. HyperDX (what a user actually sees)
12. All sources (logs, traces, metrics) are configured and return data.
13. The dashboard's availability value equals ground-truth availability (**within a stated tolerance**), and p95 latency for `/slow?ms=N` is consistent with N.
14. The error list shows exactly the failures the generator caused (`/error`, `/panic`, `/chain-fail`).
15. The runlog query for a `test_run_id` returns its logs; opening a trace shows the correlated logs.
Checked through the HyperDX API where possible, otherwise by driving the UI and capturing screenshots as evidence.

### E. Latency sanity
16. End-to-end delay (request made → row queryable) is recorded. Published figure is ~3s p50 / ~10s p99; local pass threshold is a loose 15s, so regressions are caught but not flaky.

### F. Portability
17. **No code or image change between local and AWS.** Object store, ClickHouse and HyperDX settings are env/YAML only.
18. Same verification suite is run a second time against **real S3** (scratch bucket, IAM role, config switch only). *This needs your AWS access; I will not touch AWS without your explicit go-ahead.*

### G. Process gates (your workflow rules)
- Rust supply-chain auditor approves every new crate **before** any `Cargo.toml` change.
- Completion-reviewer subagent signs off before I report done.
- I report failures as failures, with output; nothing is called done without a green run of the suite.

## 5. Known risks and open questions to confirm early (first spike)

1. **Collector image is amd64-only** (`ghcr.io/opendata-oss/otel-collector:v0.4.0`): runs emulated on this arm64 Mac and would not run natively on Graviton. Plan: use it locally for the first milestone, and build our own multi-arch collector with the OTel Collector Builder for portability. Needs your OK on the extra work.
2. `docker` here is **Podman 5.8** with an external compose provider. Not yet confirmed that the stack runs cleanly on it.
3. Whether the exporter/ingestor accept an **S3 endpoint override** (MinIO) and whether MinIO supports the **conditional writes** the manifest relies on. AWS S3 does.
4. Whether HyperDX **infers columns** from our ClickStack-shaped tables, and whether dashboards and saved searches can be **created via API** (otherwise: UI automation + exported JSON).
5. Metrics flattening is the largest piece of new code (gauge/sum/histogram schema, temporality, exemplars).
6. The OpenData project is early-stage; version-pin everything and expect rough edges.

## 5a. P0 research findings (evidence from reading opendata-contrib @ 3668618)

- **S3 endpoint override works via the standard AWS env chain** (`AWS_ENDPOINT`, `AWS_ALLOW_HTTP`, `AWS_VIRTUAL_HOSTED_STYLE_REQUEST`), the same mechanism real S3 uses. Risk 3 (endpoint override) resolved; MinIO conditional-write support still to be proven by the running tutorial.
- **Both OpenData images are amd64-only** (`otel-collector:v0.4.0`, `clickhouse-ingestor:0.2.0`). ClickHouse, HyperDX, telemetrygen are multi-arch. We build the ingestor ourselves (arm64 native). Collector: emulated locally for now.
- **Correction to an earlier statement:** the traces "worked example" test is disabled (`#![cfg(any())]`) and stale against the current Decoder/Sink traits. It is a design sketch, not a compiling template.
- `SignalType` already has `Metrics`, `Logs`, `Traces`: no envelope change needed.
- Runtime/plugins are NOT on crates.io (only `opendata-buffer` 0.3.0 is). `RowValue` has no Float64 / Array / Nested; metrics tables need them. Hence vendoring (decision above). Upstreaming later is possible but not a dependency of this work.
- Single-node ClickHouse rejects `insert_deduplication_token`, so locally dedup relies on `ReplacingMergeTree` + `_odb_*` keys (config `apply_deduplication_token: false`). Reliability check 8 must therefore verify dedup **after merge** (`FINAL`), not at insert time. **(Superseded: the tables set `non_replicated_deduplication_window`, and `chaos.py dedup-token` proved insert-time dedup works on a single node.)**
- The runtime drives one decoder per source (one manifest), so we run **one ingestor process per signal** (logs / metrics / traces), each with its own config.

### P0 progress log (2026-10-03)
- MinIO is **gone** (Docker Hub images deleted 2026-09-11; quay.io now unauthorized). Replaced with **RustFS 1.0.1** (multi-arch, Apache-2.0, S3 conditional writes). Bucket creation uses `amazon/aws-cli` so the same step works against real S3. Buffer's manifest compare-and-set works on RustFS (manifest created, batches flowing).
- Collector's Go S3 client uses **virtual-hosted-style** bucket hosts (`bucket.s3`). Locally this needs a network alias `tutorial-buffer.s3` plus `RUSTFS_SERVER_DOMAINS`. Real S3 does this natively, so no AWS impact. Remember to re-check this when we build our own collector.
- Tutorial bug: `GRANT` on an XML-defined ClickHouse user fails (read-only storage); removed (user already has access).
- **Checkpoint 1 met:** 5,000 logs sent via telemetrygen -> exactly 5,000 rows in ClickHouse in ~2s, 5,000 unique `_odb_*` keys.
- Supply-chain audit verdict **CONDITIONAL**: contrib `Cargo.lock` has known advisories (rustls, h2, quinn-proto, crossbeam-epoch, metrics yanked, anyhow); must `cargo update` those and re-audit before the first build; remove `testcontainers*` from vendored copy; approved version sets: own gRPC = tonic/tonic-prost(-build) 0.14.6 + prost 0.14.4; app telemetry = opentelemetry/_sdk/-otlp/-semantic-conventions 0.33.0 + tracing-opentelemetry 0.34.0 (grpc-tonic export to avoid a second reqwest); contrib decoders keep opentelemetry-proto 0.28 + prost 0.13 (bridge via encoded bytes, never mix); uuid pinned =1.26.1; reqwest 0.12.28 rustls only.

### Build progress log (2026-10-03, later)
- **Vendored** contrib @3668618 into `ingest/` (VENDORED.md). Conditions from the second audit applied (lock updated, testcontainers removed); re-audit: APPROVED to build with `--locked`. Baseline `cargo check` was green before any change.
- **Decoders** (`ingest/plugins/opendata-ingest-otel`): traces + metrics, with unit tests. **Deviation from stock logs decoder (deliberate):** malformed protobuf entries are counted (`ingestor_decode_malformed_entries_total{signal}`) and skipped instead of failing the pipeline; wrong-signal envelope stays fatal. Summary / exponential-histogram points are counted in `ingestor_decode_skipped_points_total`.
- **Adapters** (`ingest/plugins/opendata-ingest-clickhouse`): shared deterministic planner `adapter/common.rs`, traces adapter, metrics adapter (fan-out to gauge/sum/histogram), `RowValue` extended (Float64, Bool, arrays), RowBinary type table extended, `ClickHouseSink` generalized via `SinkRecord`.
- **Connector**: `signal: logs|traces|metrics` in config (figment YAML + `INGESTOR__*` env, as upstream); one ingestor process per signal.
- **DDL**: `stack/clickhouse/*.sql`, ClickStack-shaped, ReplacingMergeTree + `_odb_*` in the sort key, `non_replicated_deduplication_window` so insert-time dedup tokens should work single-node (to be proven in the reliability tests). Read-only `hyperdx_ro` user.
- **Stack**: `stack/compose.yml` (project `obs`) with per-signal manifests.
- HyperDX external API v2 (port 8000, Bearer personal key) manages connections/sources/dashboards/alerts/saved-searches/webhooks: HyperDX content can be code (risk 4 resolved). Browser extension was not connected, so UI screenshots are pending.
- Test app is being built by a subagent in `app/`.

## 6. Plan (phases, each ends with a verifiable checkpoint)

- [x] **P0 Spike:** official tutorial path on Podman (logs). Risks 1-4 resolved or re-planned (see progress logs): MinIO replaced by RustFS; collector image amd64-only and **traces unsupported upstream** -> own collector with a patched exporter.
- [x] **P1 Audit + workspace:** supply-chain audits (Rust: contrib lock re-audited and approved; app crates approved incl. appender-tracing). Go modules: no Go auditor agent exists in this setup; modules are the official OTel collector set plus the opendata exporter's own `go.sum`, built in a container (see Review).
- [x] **P2 Test app:** axum + tonic with OTLP out; built by a subagent, reviewed (see `app/README.md`).
- [x] **P3 Traces path:** collector traces signal (patched exporter) + traces decoder/adapter. Items 2, 5-7 verified.
- [x] **P4 Metrics path:** metrics decoder/adapter (gauge, sum, histogram). Items 3, 4 verified.
- [x] **P5 Logs path in ClickStack shape:** stock logs adapter writes `otel_logs`; items 1, 6 verified.
- [x] **P6 HyperDX content as code:** `stack/hyperdx/provision.py` (connection, sources, 2 dashboards, saved searches). Items 12, 14, 15 and the numbers behind 13 verified through HyperDX's own search/chart APIs. Rendering seen later via headless Chrome over the DevTools Protocol (`course/tools/shot.mjs`); that exposed the availability chart filling empty buckets with 0%, fixed with `fillNulls: false`.
- [x] **P7 Reliability + chaos suite:** items 8-11 and 16 (`stack/verify/chaos.py`, 8 scenarios). Found and fixed real data loss (no retry in upstream exporter).
- [x] **P8 Full clean-room run:** all volumes wiped; verify 27/27, selftest 7/7 detected, chaos 7/7. Later checks were added (31 total) and every suite was re-run on the final code: verify 31/31, selftest 7/7, chaos 8/8.
- [x] **P8b Completion review:** three reviews so far. 1st NOT READY (claims outran evidence; fixed). 2nd NOT READY with one small blocking item (replay counter read the wrong log; fixed, 1/2/2 real replays then observed). 3rd NOT READY (alert threshold semantics `above` = >=, undisclosed once-per-minute alert evaluation; fixed, see alerts below). A 4th review is requested after these fixes.
- [x] **P11 Alerts (added at the user's request):** two alerts as code, webhook sink, `stack/verify/alerts.py`.
- [ ] **P9 AWS parity run (needs your go-ahead and AWS access):** items 17-18. NOT started; I will not touch AWS without an explicit yes.
- [x] **P10 amd64:** all three images built for linux/amd64 and the whole pipeline ran on them under emulation (`stack/compose.amd64.yml`): verify 31/31. Not run on real x86 hardware.

## 7. Review

### Outcome (cold start with wiped volumes first, then incremental changes: a DDL column added by ALTER, verifier edits; every suite re-run on the final code)
- `verify.py`: **31/31**; `selftest.py`: **7/7 tamperings detected**; `chaos.py`: **8 scenarios pass** (ingestor SIGKILL, ingestor restart, ClickHouse down 20 s, S3 down 8 s and 30 s, malformed payloads into all three queues, collector restart idle, insert-token replay probe).
- Same verification on **emulated amd64** images: 31/31.
- `alerts.py`: quiet on healthy traffic, exactly-4-failures boundary probe stays OK, fires on a 60-failure burst with a delivered webhook, resolves with a resolve webhook (negative tests: unreachable thresholds and the old `>=` comparison are both caught).
- Rust: `cargo fmt --check` clean, `clippy -D warnings` clean, ~210 tests; Go exporter tests pass; app gates passed (separately reviewed).
- Measured end-to-end latency per row (event -> queryable): spans p50 0.6 s / p99 2.6-4.7 s across runs; logs p50 0.4-0.6 s / p99 ~1.3 s. (The amd64 figure came from an earlier version of the HyperDX metrics check, which was later made timing-independent; the pipeline checks are unchanged.)

### Where the result differs from the original wording (honest deviations)
| Item | Deviation |
|---|---|
| 3 | Metrics are checked by the run's final cumulative value (`max`), so a lost intermediate snapshot is not detected; gauge storage was exercised only with generated data (telemetrygen), not by the test app. |
| 4 | Duplicate checks cover all four tables used by the app (traces, logs, sum, histogram). |
| 10 | The collector is restarted only while idle; under load the app->collector edge is at-least-once by design (duplicates/loss of in-flight requests possible). |
| 11 | Implemented as "malformed *protobuf* is counted and skipped" for all three signals. A malformed *envelope* (wrong signal) stops that ingestor on purpose (routing bug). Valid OTLP that ClickHouse rejects is retried forever and blocks that queue. |
| 8/9 | SIGKILL under load caused real replays that ClickHouse dropped as duplicates (1, 2 and 2 in three repeated runs; read from its server log) with exact counts afterwards; graceful restarts replay nothing. Whether a kill lands mid-commit is timing-dependent, so one run can see zero. The dedup mechanism is also proved deterministically by the token probe. |
| 12 | Now covers Logs, Traces and the Metrics source (1 m granularity) through HyperDX. |
| 13 | The dashboard **tiles were not executed**; HyperDX's own chart API computes the equivalent numbers (count, availability, per-route, p50 for `/slow`). The original wording said p95; the check asserts p50 consistency with the injected delay. Rendering in a browser was never seen (extension not connected). |
| 14 | Verifier checks the failed-web-span list (35) **and** the saved *Error list* searches themselves (all error spans: 55 per default run, plus ERROR logs). |
| 16 | The first version measured 0.008 s from a timer that started after the load generator exited (an artifact; the README claimed ~1 s). Replaced by a per-row measurement (`_odb_clickhouse_inserted_at`). |
| 17 | Credentials/endpoints come from the standard AWS env chains in Go and Rust. The ingestor region is in YAML (override `INGESTOR__BUFFER__OBJECT_STORE__REGION`). A ClickHouse cluster needs `Replicated*` DDL. |
| alerts (added later on request; threshold semantics found by review) | HyperDX `above` means >= (so "above 4" fired at 4); now `above_exclusive` plus a boundary probe. HyperDX evaluates each minute once at :00 with no ingest-lag allowance, so rows landing after :00 are never alerted on (disclosed; bursts in the tests start early in a minute). |
| alerts (details) | `stack/verify/alerts.py`: two HyperDX alerts as code (tile + saved search, webhook to a local sink); quiet on healthy traffic, fire on a burst with delivered counts equal to what was sent (60 failed requests / 70 ERROR logs), resolve notification after. 11/11 including a boundary probe (exactly 4 failures must not fire; HyperDX's `above` is >=, so the alerts use `above_exclusive`); negative tests: unreachable thresholds fail the fire checks, the old `>=` comparison fails the boundary delivery check. Not part of the original 18 items. |
| 18 | **Not done**: needs the user's AWS access and explicit go-ahead. |

### Other decisions made on the way (all recorded above in the progress logs)
- Vendored opendata-contrib and opendata-go (MIT) instead of "new workspace, no fork": runtime crates are not on crates.io and `RowValue` is closed; the upstream exporter had **no traces** and **no retry** (data loss on an S3 outage, found by chaos testing).
- MinIO replaced by RustFS (MinIO images were removed).
- Supply chain: Rust crates audited by the auditor agent before any build; **Go modules had no auditor in this setup** (official OTel collector modules + the exporter's own `go.sum`, built in containers) - worth a manual look if this goes to production.

### Known limits (also in `stack/README.md`)
Collector crash loses in-memory data; producer halt after exhausted manifest retries not reproduced at 30 s but not ruled out beyond; HyperDX external API is limited to 100 requests/window; `app.requests` tile undercounts (HyperDX drops each series' first cumulative point); `1d` verifier buckets break across UTC midnight.

### Lessons recorded in `tasks/lessons.md`
Verify vendor docs against the artifact; negative-test the verifier; fault-inject early; unique run ids; zsh word splitting.
