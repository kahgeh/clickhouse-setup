# sre test app

Telemetry generator for the observability stack (OTel -> OpenData Buffer -> ClickHouse -> HyperDX).
It produces logs, metrics and traces (including HTTP -> gRPC distributed traces) with exactly known
counts, so a verification script can compare ClickHouse against `ground_truth.json`.

Crates: `contract` (endpoint table, ground-truth types, proto), `telemetry` (OTel init),
`backend` (gRPC), `web` (HTTP), `loadgen` (load generator + ground truth).

## Run

```sh
# backend (gRPC, :50051)
SERVICE_NAME=backend BACKEND_ADDR=0.0.0.0:50051 \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 cargo run --release -p backend
# web (HTTP, :8081)
SERVICE_NAME=web WEB_ADDR=0.0.0.0:8081 BACKEND_URL=http://localhost:50051 \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 cargo run --release -p web
# load
cargo run --release -p loadgen -- --base-url http://localhost:8081 --run-id demo-1 \
  --counts ok=100,slow=20,error=20,exception=5,chain=50,chain-fail=10,checkout=20 \
  --slow-ms 200 --concurrency 4 --out ground_truth.json
```

| env | default | meaning |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP/gRPC endpoint. Unset = no exporter, no network (spans still get real ids) |
| `SERVICE_NAME` | `web` / `backend` | `service.name` resource attribute |
| `OTEL_METRIC_EXPORT_INTERVAL_MS` | `5000` | metric export interval |
| `WEB_ADDR` | `0.0.0.0:8081` | web listen address |
| `BACKEND_ADDR` | `0.0.0.0:50051` | backend listen address |
| `BACKEND_URL` | `http://localhost:50051` | backend URL used by web |

Resource attributes: `service.name`, `service.version`, `deployment.environment.name=local`.
Only this app's own events (targets `web`, `backend`, `telemetry`, `contract`, `loadgen`) at info+
become log records or spans; hyper/h2/tonic/tower/opentelemetry internals are never emitted.
Startup chatter goes to stderr, not to tracing.

Docker: `docker build -t sre-app:dev .` then run `web`, `backend` or `loadgen` as the command
(binaries in `/usr/local/bin`).

## Function-level spans (`trace_macros`)

`#[trace_all]` (crate `trace_macros`, a proc macro on `syn`/`quote`) adds `#[tracing::instrument(skip_all)]` to every fn of an `impl` block or inline `mod`; `skip(name, ..)` spares named fns, a fn with its own `#[instrument]` and any `const fn` are left alone, and a `skip` name that matches nothing is a compile error. The fictitious `/checkout` flow uses it in both services. Function spans do not carry `test_run_id` themselves, so `telemetry::RunIdPropagator` (a span processor) copies it from the parent span.

## Contract table (per request)

| endpoint | HTTP status | spans (one trace) | log records |
|---|---|---|---|
| GET /ok | 200 | 1: SERVER `GET /ok` | 1 INFO |
| GET /slow?ms=N | 200 after N ms (default 200) | 1: SERVER `GET /slow` | 1 INFO |
| GET /error | 500 | 1: SERVER `GET /error`, status Error | 1 ERROR |
| GET /exception | 500 | 1: SERVER `GET /exception`, Error, `exception` event (`exception.type`, `exception.message`) | 1 ERROR |
| GET /chain | 200 | 4: web SERVER `GET /chain` -> web CLIENT `backend.Process` -> backend SERVER `backend.Process` -> backend INTERNAL `backend.compute` | 2 INFO (web, backend) |
| GET /chain-fail | 502 | 3: web SERVER (Error) -> web CLIENT (Error) -> backend SERVER (Error) | 2 ERROR (web, backend) |
| GET /checkout | 200 | 22: function-level tree, 9 function spans in web (`price_items` has its own `#[instrument]`, the other 8 come from `#[trace_all]`) + 10 in backend, plus the hand-written server/client/server spans (see `web/src/checkout.rs`) | 2 INFO (web, backend) |
| GET /health | 200 | none | none (no metrics either) |
| POST /admin/flush | 200 | none | none; flushes web providers and calls backend `Flush` |

The table lives in `contract/src/lib.rs` (`Endpoint`) and is unit-tested.

Every request carries `x-test-run-id` (default `none`), recorded as attribute `test_run_id` on spans,
log records and metrics. Every response except `/health` and `/admin/flush` has `x-trace-id`
(32 hex chars, trace id of the server span). Trace context and the run id are forwarded to the
backend via gRPC metadata (`traceparent`, `x-test-run-id`).

Metrics (cumulative, histogram buckets 0.005 ... 5 s):
- web: `http.server.request.duration` (s; `http.request.method`, `http.route`,
  `http.response.status_code`, `test_run_id`), `http.server.active_requests` (`http.route`),
  `app.requests` (`service=web`, `route`, `outcome=ok|error` where error = status >= 500, `test_run_id`)
- backend: `rpc.server.duration` (s; `rpc.method`, `rpc.grpc.status_code`, `test_run_id`),
  `app.requests` (`service=backend`, `route=Process`, `outcome`, `test_run_id`)

## ground_truth.json

```json
{
  "run_id": "demo-1",
  "started_at_unix_nano": 0,
  "ended_at_unix_nano": 0,
  "base_url": "http://localhost:8081",
  "per_endpoint": {
    "<ok|slow|error|exception|chain|chain-fail|checkout>": {
      "count": 0,
      "expected_status": 200,
      "actual_status_counts": { "200": 0 },
      "expected_spans_each": 1,
      "expected_logs_each": 1
    }
  },
  "totals": {
    "requests": 0, "expected_spans": 0, "expected_logs": 0,
    "expected_http_5xx": 0, "expected_availability": 1.0
  },
  "trace_ids": { "<endpoint>": ["<32 hex>"] }
}
```

Expectations come from the contract table, never from observed telemetry. `expected_availability`
= non-5xx / total. Requests are built from the counts and interleaved round-robin by endpoint
(order: ok, slow, error, exception, chain, chain-fail, checkout), no randomness. loadgen calls
`POST /admin/flush` at the end and exits non-zero if any status differs from `expected_status`,
a trace id is missing, a request fails, or the flush fails (the file is still written).

## Tests / debug collector

`cargo test --workspace --locked` (offline; in-process backend + web). `dev/collector-debug.yaml` is
a debug-only collector config (otlp in, `debug` exporter out) for checking raw telemetry:

```sh
docker run -d --name app-otel-debug --platform linux/amd64 -p 14317:4317 \
  -v $PWD/dev/collector-debug.yaml:/etc/otel/config.yaml:ro \
  ghcr.io/opendata-oss/otel-collector:v0.4.0 --config=/etc/otel/config.yaml
```
