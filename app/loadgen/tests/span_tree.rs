//! Runs backend and web in-process and collects every span they produce, to check that the
//! call tree behind each endpoint matches the span count and run id in the contract.
//!
//! Own test binary: it installs the global tracing subscriber (telemetry's `init` then leaves it
//! alone), with the same run id propagation as production.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use contract::{Endpoint, HEADER_RUN_ID, HEADER_TRACE_ID};
use opentelemetry::Context;
use opentelemetry::trace::TracerProvider as _;
use opentelemetry_sdk::error::OTelSdkResult;
use opentelemetry_sdk::trace::{SdkTracerProvider, Span, SpanData, SpanProcessor};
use tokio::net::TcpListener;
use tracing_subscriber::Layer;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

#[derive(Debug, Clone, Default)]
struct Collector(Arc<Mutex<Vec<SpanData>>>);

impl SpanProcessor for Collector {
    fn on_start(&self, _span: &mut Span, _cx: &Context) {}

    fn on_end(&self, span: SpanData) {
        self.0.lock().unwrap().push(span);
    }

    fn force_flush(&self) -> OTelSdkResult {
        Ok(())
    }

    fn shutdown_with_timeout(&self, _timeout: Duration) -> OTelSdkResult {
        Ok(())
    }
}

fn attr(span: &SpanData, key: &str) -> Option<String> {
    span.attributes
        .iter()
        .find(|kv| kv.key.as_str() == key)
        .map(|kv| kv.value.to_string())
}

#[tokio::test]
async fn every_endpoint_produces_the_expected_span_tree() {
    let collector = Collector::default();
    let provider = SdkTracerProvider::builder()
        .with_span_processor(telemetry::RunIdPropagator::default())
        .with_span_processor(collector.clone())
        .build();
    tracing_subscriber::registry()
        .with(
            tracing_opentelemetry::layer()
                .with_tracer(provider.tracer("test"))
                .with_filter(telemetry::app_targets()),
        )
        .init();

    let backend_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let backend_url = format!("http://{}", backend_listener.local_addr().unwrap());
    let web_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", web_listener.local_addr().unwrap());
    tokio::spawn(backend::serve(backend_listener, std::future::pending()));
    tokio::spawn(
        async move { web::serve(web_listener, &backend_url, std::future::pending()).await },
    );

    let client = reqwest::Client::new();
    let mut trace_ids = BTreeMap::new();
    for e in Endpoint::ALL {
        let r = client
            .get(format!("{base}{}?ms=1", e.path()))
            .header(HEADER_RUN_ID, "run-42")
            .send()
            .await
            .unwrap();
        assert_eq!(r.status().as_u16(), e.expected_status(), "{}", e.name());
        trace_ids.insert(
            e,
            r.headers()[HEADER_TRACE_ID].to_str().unwrap().to_string(),
        );
    }

    let spans = collector.0.lock().unwrap();
    for e in Endpoint::ALL {
        let trace: Vec<&SpanData> = spans
            .iter()
            .filter(|s| s.span_context.trace_id().to_string() == trace_ids[&e])
            .collect();
        assert_eq!(
            trace.len() as u64,
            e.expected_spans(),
            "{}: {:?}",
            e.name(),
            trace
                .iter()
                .map(|s| (&s.name, &s.instrumentation_scope))
                .collect::<Vec<_>>()
        );
        assert!(
            trace
                .iter()
                .all(|s| attr(s, "test_run_id").as_deref() == Some("run-42")),
            "{}: a span lacks test_run_id",
            e.name()
        );
        // One root, every other span has its parent in the same trace.
        let ids: Vec<_> = trace.iter().map(|s| s.span_context.span_id()).collect();
        let roots = trace
            .iter()
            .filter(|s| !ids.contains(&s.parent_span_id))
            .count();
        assert_eq!(roots, 1, "{}: expected a single root", e.name());
    }

    if let Some(trace_id) = trace_ids.get(&Endpoint::Checkout) {
        let mut names: Vec<String> = spans
            .iter()
            .filter(|s| s.span_context.trace_id().to_string() == *trace_id)
            .map(|s| s.name.to_string())
            .collect();
        names.sort();
        let mut expected = [
            "GET /checkout",
            "place_order",
            "validate_cart",
            "load_cart",
            "price_items",
            "unit_price",
            "tax",
            "apply_discount",
            "reserve_inventory",
            "backend.Checkout",
            "backend.Checkout",
            "confirm_order",
            "lookup_stock",
            "read_shelf",
            "check_fraud",
            "load_history",
            "score",
            "authorise_payment",
            "call_acquirer",
            "write_ledger",
            "debit",
            "credit",
        ]
        .map(String::from);
        expected.sort();
        assert_eq!(names, expected);
    }
}
