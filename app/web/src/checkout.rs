//! Fictitious checkout flow behind `GET /checkout`, one span per function.
//!
//! `CheckoutService` and the `pricing` module are annotated with `#[trace_all]`, so every
//! function is a span. The call tree is fixed (the sleeps only give the waterfall visible
//! widths), which makes the number of spans per request a constant: 9 function spans and 2
//! hand-written ones (marked `*`) here, 11 in the backend, 22 per request in total
//! (`Endpoint::Checkout::expected_spans` in `contract`, asserted by `loadgen/tests/span_tree.rs`):
//!
//! ```text
//! http.request                    * server span from the middleware
//! └─ place_order
//!    ├─ validate_cart
//!    │  └─ load_cart
//!    ├─ price_items              (loops over 5 cart lines, no span per line; records items and slowest_line_ms)
//!    │  ├─ unit_price             (mod pricing)
//!    │  └─ tax                    (mod pricing)
//!    ├─ apply_discount
//!    ├─ reserve_inventory
//!    │  └─ backend.Checkout       * client span
//!    │     └─ backend.Checkout    * server span in backend, plus its 10 function spans
//!    └─ confirm_order
//! ```

use std::time::Duration;

use contract::HEADER_RUN_ID;
use contract::pb::CheckoutRequest;
use contract::pb::backend_client::BackendClient;
use opentelemetry::trace::Status as SpanStatus;
use tonic::metadata::MetadataValue;
use tonic::transport::Channel;
use trace_macros::trace_all;
use tracing::Instrument;
use tracing_opentelemetry::OpenTelemetrySpanExt;

/// Simulated work per cart line, in milliseconds (five lines).
const CART_LINE_COST_MS: [u64; 5] = [1, 1, 2, 1, 3];

async fn pause(ms: u64) {
    tokio::time::sleep(Duration::from_millis(ms)).await;
}

pub struct CheckoutService {
    backend: BackendClient<Channel>,
    run_id: String,
}

#[trace_all(skip(new))]
impl CheckoutService {
    pub fn new(backend: BackendClient<Channel>, run_id: String) -> Self {
        CheckoutService { backend, run_id }
    }

    pub async fn place_order(&self) -> Result<(), tonic::Status> {
        self.validate_cart().await;
        self.price_items().await;
        self.apply_discount().await;
        self.reserve_inventory().await?;
        self.confirm_order().await;
        Ok(())
    }

    async fn validate_cart(&self) {
        pause(1).await;
        self.load_cart().await;
    }

    async fn load_cart(&self) {
        pause(2).await;
    }

    /// The loop is one span, not one span per line: the span covers the loop plus the two pricing calls, and
    /// `items` / `slowest_line_ms` say how long it ran and how bad its worst pass was. It carries its
    /// own `#[instrument]` to declare those fields, which `#[trace_all]` leaves alone.
    #[tracing::instrument(skip_all, fields(items = tracing::field::Empty, slowest_line_ms = tracing::field::Empty))]
    async fn price_items(&self) {
        let mut slowest = 0;
        for line_ms in CART_LINE_COST_MS {
            let started = std::time::Instant::now();
            pause(line_ms).await;
            slowest = slowest.max(started.elapsed().as_millis() as u64);
        }
        let span = tracing::Span::current();
        span.record("items", CART_LINE_COST_MS.len() as u64);
        span.record("slowest_line_ms", slowest);
        pricing::unit_price().await;
        pricing::tax().await;
    }

    async fn apply_discount(&self) {
        pause(1).await;
    }

    /// Calls `backend.Checkout` inside a CLIENT span, propagating trace context and run id.
    async fn reserve_inventory(&self) -> Result<(), tonic::Status> {
        let span = tracing::info_span!(
            "backend.Checkout",
            otel.kind = "client",
            rpc.system = "grpc",
            rpc.service = "sre.backend.Backend",
            rpc.method = "Checkout",
            test_run_id = %self.run_id,
        );
        async {
            let mut client = self.backend.clone();
            let mut request = tonic::Request::new(CheckoutRequest {
                cart_id: "cart-1".to_string(),
            });
            telemetry::inject_context(&tracing::Span::current().context(), request.metadata_mut());
            if let Ok(v) = MetadataValue::try_from(self.run_id.as_str()) {
                request.metadata_mut().insert(HEADER_RUN_ID, v);
            }
            let result = client.checkout(request).await.map(|_| ());
            if let Err(status) = &result {
                tracing::Span::current()
                    .set_status(SpanStatus::error(status.message().to_string()));
            }
            result
        }
        .instrument(span)
        .await
    }

    async fn confirm_order(&self) {
        pause(1).await;
    }
}

#[trace_all]
mod pricing {
    use super::pause;

    pub async fn unit_price() {
        pause(2).await;
    }

    pub async fn tax() {
        pause(1).await;
    }
}
