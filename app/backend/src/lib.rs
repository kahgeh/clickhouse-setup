//! gRPC backend: `Process` (optionally failing), `Checkout` and `Flush`, plus the standard health service.

use std::future::Future;
use std::time::{Duration, Instant};

use contract::pb::backend_server::{Backend, BackendServer};
use contract::pb::{CheckoutReply, CheckoutRequest, Empty, ProcessReply, ProcessRequest};
use contract::{DEFAULT_RUN_ID, HEADER_RUN_ID};
use opentelemetry::metrics::{Counter, Histogram};
use opentelemetry::trace::Status as SpanStatus;
use telemetry::{LATENCY_BUCKETS, kv};
use tokio::net::TcpListener;
use tokio_stream::wrappers::TcpListenerStream;
use tonic::{Code, Request, Response, Status};
use tracing::Instrument;
use tracing_opentelemetry::OpenTelemetrySpanExt;

pub mod checkout;

struct Metrics {
    duration: Histogram<f64>,
    requests: Counter<u64>,
}

impl Metrics {
    fn new(meter: &opentelemetry::metrics::Meter) -> Self {
        Metrics {
            duration: meter
                .f64_histogram("rpc.server.duration")
                .with_unit("s")
                .with_boundaries(LATENCY_BUCKETS.to_vec())
                .build(),
            requests: meter.u64_counter("app.requests").build(),
        }
    }
}

pub struct BackendSvc {
    metrics: Metrics,
}

impl BackendSvc {
    pub fn new() -> Self {
        let meter = telemetry::init("backend").meter("backend");
        BackendSvc {
            metrics: Metrics::new(&meter),
        }
    }

    fn record(&self, method: &'static str, code: Code, run_id: &str, started: Instant) {
        self.metrics.duration.record(
            started.elapsed().as_secs_f64(),
            &[
                kv("rpc.method", method),
                kv("rpc.grpc.status_code", code as i64),
                kv("test_run_id", run_id.to_string()),
            ],
        );
        self.metrics.requests.add(
            1,
            &[
                kv("service", "backend"),
                kv("route", method),
                kv("outcome", if code == Code::Ok { "ok" } else { "error" }),
                kv("test_run_id", run_id.to_string()),
            ],
        );
    }
}

impl Default for BackendSvc {
    fn default() -> Self {
        Self::new()
    }
}

#[tonic::async_trait]
impl Backend for BackendSvc {
    async fn process(
        &self,
        request: Request<ProcessRequest>,
    ) -> Result<Response<ProcessReply>, Status> {
        let started = Instant::now();
        let run_id = request
            .metadata()
            .get(HEADER_RUN_ID)
            .and_then(|v| v.to_str().ok())
            .unwrap_or(DEFAULT_RUN_ID)
            .to_string();
        let parent = telemetry::extract_context(request.metadata());
        let fail = request.get_ref().fail;

        let span = tracing::info_span!(
            parent: None,
            "backend.Process",
            otel.kind = "server",
            rpc.system = "grpc",
            rpc.service = "sre.backend.Backend",
            rpc.method = "Process",
            test_run_id = %run_id,
        );
        let _ = span.set_parent(parent);

        let result = {
            let span = span.clone();
            let run_id = run_id.clone();
            async move {
                if fail {
                    tracing::error!(test_run_id = %run_id, "backend failure");
                    span.set_status(SpanStatus::error("backend failure"));
                    return Err(Status::internal("backend failure"));
                }
                tokio::time::sleep(Duration::from_millis(5))
                    .instrument(tracing::info_span!(
                        "backend.compute",
                        otel.kind = "internal",
                        test_run_id = %run_id
                    ))
                    .await;
                tracing::info!(test_run_id = %run_id, "backend processed");
                Ok(Response::new(ProcessReply {
                    message: "processed".to_string(),
                }))
            }
        }
        .instrument(span)
        .await;

        let code = result.as_ref().map_or_else(|s| s.code(), |_| Code::Ok);
        self.record("Process", code, &run_id, started);
        result
    }

    async fn checkout(
        &self,
        request: Request<CheckoutRequest>,
    ) -> Result<Response<CheckoutReply>, Status> {
        let started = Instant::now();
        let run_id = request
            .metadata()
            .get(HEADER_RUN_ID)
            .and_then(|v| v.to_str().ok())
            .unwrap_or(DEFAULT_RUN_ID)
            .to_string();
        let parent = telemetry::extract_context(request.metadata());
        let cart_id = request.get_ref().cart_id.clone();

        let span = tracing::info_span!(
            parent: None,
            "backend.Checkout",
            otel.kind = "server",
            rpc.system = "grpc",
            rpc.service = "sre.backend.Backend",
            rpc.method = "Checkout",
            test_run_id = %run_id,
        );
        let _ = span.set_parent(parent);

        let result = {
            let span = span.clone();
            let run_id = run_id.clone();
            async move {
                match checkout::process(&cart_id).await {
                    Ok(order_id) => {
                        tracing::info!(test_run_id = %run_id, "backend checkout completed");
                        Ok(Response::new(CheckoutReply { order_id }))
                    }
                    Err(reason) => {
                        tracing::error!(test_run_id = %run_id, reason, "backend checkout rejected");
                        span.set_status(SpanStatus::error(reason));
                        Err(Status::failed_precondition(reason))
                    }
                }
            }
        }
        .instrument(span)
        .await;

        let code = result.as_ref().map_or_else(|s| s.code(), |_| Code::Ok);
        self.record("Checkout", code, &run_id, started);
        result
    }

    async fn flush(&self, _request: Request<Empty>) -> Result<Response<Empty>, Status> {
        tokio::task::spawn_blocking(telemetry::force_flush_all)
            .await
            .map_err(|e| Status::internal(format!("flush task failed: {e}")))?;
        Ok(Response::new(Empty {}))
    }
}

/// Serves the backend (and health) on `listener` until `shutdown` completes.
pub async fn serve(
    listener: TcpListener,
    shutdown: impl Future<Output = ()> + Send + 'static,
) -> anyhow::Result<()> {
    let (reporter, health) = tonic_health::server::health_reporter();
    let svc = BackendServer::new(BackendSvc::new());
    reporter.set_serving::<BackendServer<BackendSvc>>().await;
    tonic::transport::Server::builder()
        .add_service(health)
        .add_service(svc)
        .serve_with_incoming_shutdown(TcpListenerStream::new(listener), shutdown)
        .await?;
    Ok(())
}
