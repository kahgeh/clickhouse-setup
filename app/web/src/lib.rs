//! HTTP front service. Every telemetry-producing endpoint follows the table in the
//! `contract` crate; `/health` and `/admin/flush` produce no telemetry at all.

use std::future::Future;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::{MatchedPath, RawQuery, Request, State};
use axum::http::{HeaderValue, StatusCode};
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::routing::{get, post};
use axum::{Extension, Router};
use contract::pb::backend_client::BackendClient;
use contract::pb::{Empty, ProcessRequest};
use contract::{
    DEFAULT_RUN_ID, DEFAULT_SLOW_MS, Endpoint, FLUSH_PATH, HEADER_RUN_ID, HEADER_TRACE_ID,
    HEALTH_PATH,
};
use opentelemetry::KeyValue;
use opentelemetry::metrics::{Counter, Histogram, UpDownCounter};
use opentelemetry::trace::{Status as SpanStatus, TraceContextExt};
use telemetry::{LATENCY_BUCKETS, kv};
use tokio::net::TcpListener;
use tonic::metadata::MetadataValue;
use tonic::transport::{Channel, Endpoint as GrpcEndpoint};
use tracing::Instrument;
use tracing_opentelemetry::OpenTelemetrySpanExt;

pub mod checkout;

struct Metrics {
    duration: Histogram<f64>,
    active: UpDownCounter<i64>,
    requests: Counter<u64>,
}

#[derive(Clone)]
pub struct AppState {
    backend: BackendClient<Channel>,
    metrics: Arc<Metrics>,
}

impl AppState {
    /// Must be called inside a tokio runtime (the gRPC channel connects lazily).
    pub fn new(backend_url: &str) -> anyhow::Result<Self> {
        let meter = telemetry::init("web").meter("web");
        let channel = GrpcEndpoint::from_shared(backend_url.to_string())?.connect_lazy();
        Ok(AppState {
            backend: BackendClient::new(channel),
            metrics: Arc::new(Metrics {
                duration: meter
                    .f64_histogram("http.server.request.duration")
                    .with_unit("s")
                    .with_boundaries(LATENCY_BUCKETS.to_vec())
                    .build(),
                active: meter
                    .i64_up_down_counter("http.server.active_requests")
                    .build(),
                requests: meter.u64_counter("app.requests").build(),
            }),
        })
    }
}

/// Run id of the current request (header `x-test-run-id`, default `none`).
#[derive(Clone)]
struct RunId(String);

pub fn router(state: AppState) -> Router {
    let traced = Router::new()
        .route(Endpoint::Ok.path(), get(ok))
        .route(Endpoint::Slow.path(), get(slow))
        .route(Endpoint::Error.path(), get(error))
        .route(Endpoint::Exception.path(), get(exception))
        .route(Endpoint::Chain.path(), get(chain))
        .route(Endpoint::ChainFail.path(), get(chain_fail))
        .route(Endpoint::Checkout.path(), get(checkout_handler))
        .route_layer(middleware::from_fn_with_state(state.clone(), observe));
    Router::new()
        .merge(traced)
        .route(HEALTH_PATH, get(health))
        .route(FLUSH_PATH, post(flush))
        .with_state(state)
}

/// Serves web on `listener` until `shutdown` completes.
pub async fn serve(
    listener: TcpListener,
    backend_url: &str,
    shutdown: impl Future<Output = ()> + Send + 'static,
) -> anyhow::Result<()> {
    let app = router(AppState::new(backend_url)?);
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await?;
    Ok(())
}

/// Server span, HTTP semantic-convention metrics and the `x-trace-id` response header.
async fn observe(State(state): State<AppState>, mut req: Request, next: Next) -> Response {
    let started = Instant::now();
    let method = req.method().as_str().to_string();
    let path = req.uri().path().to_string();
    let route = req
        .extensions()
        .get::<MatchedPath>()
        .map_or_else(|| path.clone(), |m| m.as_str().to_string());
    let run_id = req
        .headers()
        .get(HEADER_RUN_ID)
        .and_then(|v| v.to_str().ok())
        .unwrap_or(DEFAULT_RUN_ID)
        .to_string();
    req.extensions_mut().insert(RunId(run_id.clone()));

    let span = tracing::info_span!(
        parent: None,
        "http.request",
        otel.name = %format!("{method} {route}"),
        otel.kind = "server",
        http.request.method = %method,
        http.route = %route,
        url.path = %path,
        test_run_id = %run_id,
        http.response.status_code = tracing::field::Empty,
    );

    let route_attr = [kv("http.route", route.clone())];
    state.metrics.active.add(1, &route_attr);
    let mut response = next.run(req).instrument(span.clone()).await;
    state.metrics.active.add(-1, &route_attr);

    let status = response.status().as_u16();
    span.record("http.response.status_code", i64::from(status));
    let is_error = status >= 500;
    if is_error {
        span.set_status(SpanStatus::error(format!("HTTP {status}")));
    }
    let trace_id = span.context().span().span_context().trace_id().to_string();
    if let Ok(v) = HeaderValue::from_str(&trace_id) {
        response.headers_mut().insert(HEADER_TRACE_ID, v);
    }

    let attrs: [KeyValue; 4] = [
        kv("http.request.method", method),
        kv("http.route", route.clone()),
        kv("http.response.status_code", i64::from(status)),
        kv("test_run_id", run_id.clone()),
    ];
    state
        .metrics
        .duration
        .record(started.elapsed().as_secs_f64(), &attrs);
    state.metrics.requests.add(
        1,
        &[
            kv("service", "web"),
            kv("route", route),
            kv("outcome", if is_error { "error" } else { "ok" }),
            kv("test_run_id", run_id),
        ],
    );
    response
}

async fn health() -> &'static str {
    "ok"
}

async fn ok(Extension(RunId(run_id)): Extension<RunId>) -> &'static str {
    tracing::info!(test_run_id = %run_id, "ok handled");
    "ok"
}

fn parse_ms(query: Option<&str>) -> u64 {
    query
        .into_iter()
        .flat_map(|q| q.split('&'))
        .filter_map(|kv| kv.split_once('='))
        .find(|(k, _)| *k == "ms")
        .and_then(|(_, v)| v.parse().ok())
        .unwrap_or(DEFAULT_SLOW_MS)
}

async fn slow(
    Extension(RunId(run_id)): Extension<RunId>,
    RawQuery(query): RawQuery,
) -> &'static str {
    let ms = parse_ms(query.as_deref());
    tokio::time::sleep(Duration::from_millis(ms)).await;
    tracing::info!(test_run_id = %run_id, delay_ms = ms, "slow handled");
    "slow"
}

async fn error(Extension(RunId(run_id)): Extension<RunId>) -> (StatusCode, &'static str) {
    tracing::error!(test_run_id = %run_id, "simulated error");
    (StatusCode::INTERNAL_SERVER_ERROR, "error")
}

async fn exception(Extension(RunId(run_id)): Extension<RunId>) -> (StatusCode, &'static str) {
    tracing::Span::current().add_event(
        "exception",
        vec![
            kv("exception.type", "SimulatedException"),
            kv("exception.message", "simulated exception"),
        ],
    );
    tracing::error!(test_run_id = %run_id, "simulated exception");
    (StatusCode::INTERNAL_SERVER_ERROR, "exception")
}

/// Calls `backend.Process` inside a CLIENT span, propagating trace context and run id.
async fn call_backend(state: &AppState, run_id: &str, fail: bool) -> Result<(), tonic::Status> {
    let span = tracing::info_span!(
        "backend.Process",
        otel.kind = "client",
        rpc.system = "grpc",
        rpc.service = "sre.backend.Backend",
        rpc.method = "Process",
        test_run_id = %run_id,
    );
    async {
        let mut client = state.backend.clone();
        let mut request = tonic::Request::new(ProcessRequest { fail });
        telemetry::inject_context(&tracing::Span::current().context(), request.metadata_mut());
        if let Ok(v) = MetadataValue::try_from(run_id) {
            request.metadata_mut().insert(HEADER_RUN_ID, v);
        }
        let result = client.process(request).await.map(|_| ());
        if let Err(status) = &result {
            tracing::Span::current().set_status(SpanStatus::error(status.message().to_string()));
        }
        result
    }
    .instrument(span)
    .await
}

async fn chain(
    State(state): State<AppState>,
    Extension(RunId(run_id)): Extension<RunId>,
) -> (StatusCode, &'static str) {
    match call_backend(&state, &run_id, false).await {
        Ok(()) => {
            tracing::info!(test_run_id = %run_id, "chain handled");
            (StatusCode::OK, "chain")
        }
        Err(status) => {
            tracing::error!(test_run_id = %run_id, error = %status, "chain backend call failed");
            (StatusCode::BAD_GATEWAY, "backend error")
        }
    }
}

async fn chain_fail(
    State(state): State<AppState>,
    Extension(RunId(run_id)): Extension<RunId>,
) -> (StatusCode, &'static str) {
    let outcome = call_backend(&state, &run_id, true).await;
    tracing::error!(
        test_run_id = %run_id,
        backend_failed = outcome.is_err(),
        "chain-fail: backend call failed"
    );
    (StatusCode::BAD_GATEWAY, "backend error")
}

async fn checkout_handler(
    State(state): State<AppState>,
    Extension(RunId(run_id)): Extension<RunId>,
) -> (StatusCode, &'static str) {
    let service = checkout::CheckoutService::new(state.backend.clone(), run_id.clone());
    match service.place_order().await {
        Ok(()) => {
            tracing::info!(test_run_id = %run_id, "checkout handled");
            (StatusCode::OK, "checkout")
        }
        Err(status) => {
            tracing::error!(test_run_id = %run_id, error = %status, "checkout backend call failed");
            (StatusCode::BAD_GATEWAY, "backend error")
        }
    }
}

/// Flushes web providers, then asks the backend to flush its own. No telemetry is produced.
async fn flush(State(state): State<AppState>) -> (StatusCode, &'static str) {
    if tokio::task::spawn_blocking(telemetry::force_flush_all)
        .await
        .is_err()
    {
        return (StatusCode::INTERNAL_SERVER_ERROR, "flush failed");
    }
    match state.backend.clone().flush(Empty {}).await {
        Ok(_) => (StatusCode::OK, "flushed"),
        Err(_) => (StatusCode::BAD_GATEWAY, "backend flush failed"),
    }
}

#[cfg(test)]
mod tests {
    use super::parse_ms;

    #[test]
    fn slow_query_parsing() {
        assert_eq!(parse_ms(None), 200);
        assert_eq!(parse_ms(Some("ms=15")), 15);
        assert_eq!(parse_ms(Some("x=1&ms=7")), 7);
        assert_eq!(parse_ms(Some("ms=abc")), 200);
    }
}
