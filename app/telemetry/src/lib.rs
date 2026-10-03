//! OpenTelemetry (traces, metrics, logs over OTLP/gRPC) initialisation for the test app.
//!
//! When `OTEL_EXPORTER_OTLP_ENDPOINT` is unset no exporter is created and nothing touches the
//! network, but the providers (and tracing layers) are still installed so spans carry real
//! trace ids. Must be called from within a tokio runtime when exporting (tonic requirement).

use std::borrow::Cow;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime};

use opentelemetry::global;
use opentelemetry::logs::LogRecord as _;
use opentelemetry::metrics::Meter;
use opentelemetry::propagation::{Extractor, Injector, TextMapPropagator};
use opentelemetry::trace::{Span as _, SpanId, TraceContextExt as _, TracerProvider as _};
use opentelemetry::{Context, InstrumentationScope, KeyValue};
use opentelemetry_appender_tracing::layer::OpenTelemetryTracingBridge;
use opentelemetry_otlp::{LogExporter, MetricExporter, SpanExporter};
use opentelemetry_sdk::Resource;
use opentelemetry_sdk::error::OTelSdkResult;
use opentelemetry_sdk::logs::{LogProcessor, SdkLogRecord, SdkLoggerProvider};
use opentelemetry_sdk::metrics::{PeriodicReader, SdkMeterProvider};
use opentelemetry_sdk::propagation::TraceContextPropagator;
use opentelemetry_sdk::trace::{SdkTracerProvider, Span, SpanData, SpanProcessor};
use opentelemetry_semantic_conventions::attribute::{
    DEPLOYMENT_ENVIRONMENT_NAME, SERVICE_NAME, SERVICE_VERSION,
};
use tonic::metadata::{MetadataKey, MetadataMap, MetadataValue};
use tracing::level_filters::LevelFilter;
use tracing_subscriber::Layer;
use tracing_subscriber::filter::{FilterExt, Targets, filter_fn};
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

pub const ENDPOINT_ENV: &str = "OTEL_EXPORTER_OTLP_ENDPOINT";
pub const SERVICE_NAME_ENV: &str = "SERVICE_NAME";
pub const METRIC_INTERVAL_ENV: &str = "OTEL_METRIC_EXPORT_INTERVAL_MS";
pub const DEFAULT_METRIC_INTERVAL_MS: u64 = 5000;
/// Span attribute (and log attribute) carrying the test run id.
pub const RUN_ID_ATTR: &str = "test_run_id";

/// Only events/spans whose target starts with one of these crate names are emitted.
const APP_TARGETS: [&str; 5] = ["web", "backend", "telemetry", "contract", "loadgen"];

/// Filter that lets through only our own crates, info and above: no hyper/h2/tonic/tower/
/// opentelemetry output, hence no export feedback loop and no unexpected records.
pub fn app_targets() -> Targets {
    APP_TARGETS.into_iter().fold(Targets::new(), |t, name| {
        t.with_target(name, LevelFilter::INFO)
    })
}

/// Latency histogram bucket boundaries in seconds.
pub const LATENCY_BUCKETS: [f64; 10] = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0];

/// Process-wide telemetry handles.
pub struct Telemetry {
    tracer_provider: SdkTracerProvider,
    meter_provider: SdkMeterProvider,
    logger_provider: SdkLoggerProvider,
    exporting: bool,
}

static TELEMETRY: OnceLock<Telemetry> = OnceLock::new();

/// Initialises telemetry once per process; later calls return the first instance.
///
/// `default_service_name` is used when env `SERVICE_NAME` is unset.
pub fn init(default_service_name: &str) -> &'static Telemetry {
    TELEMETRY.get_or_init(|| build(default_service_name))
}

/// Flushes all providers of the initialised telemetry (no-op if not initialised).
/// Blocking: call from `spawn_blocking` in async code.
pub fn force_flush_all() {
    if let Some(t) = TELEMETRY.get() {
        t.force_flush_all();
    }
}

fn build(default_service_name: &str) -> Telemetry {
    let exporting = std::env::var(ENDPOINT_ENV)
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false);
    let service_name =
        std::env::var(SERVICE_NAME_ENV).unwrap_or_else(|_| default_service_name.to_string());
    let resource = Resource::builder_empty()
        .with_attributes([
            KeyValue::new(SERVICE_NAME, service_name),
            KeyValue::new(SERVICE_VERSION, env!("CARGO_PKG_VERSION")),
            KeyValue::new(DEPLOYMENT_ENVIRONMENT_NAME, "local"),
        ])
        .build();

    let mut tp = SdkTracerProvider::builder()
        .with_resource(resource.clone())
        .with_span_processor(RunIdPropagator::default());
    let mut lp = SdkLoggerProvider::builder().with_resource(resource.clone());
    let mut mp = SdkMeterProvider::builder().with_resource(resource);

    if exporting {
        let interval = std::env::var(METRIC_INTERVAL_ENV)
            .ok()
            .and_then(|v| v.trim().parse::<u64>().ok())
            .unwrap_or(DEFAULT_METRIC_INTERVAL_MS);
        tp = tp.with_batch_exporter(
            SpanExporter::builder()
                .with_tonic()
                .build()
                .expect("build OTLP span exporter"),
        );
        lp = lp.with_log_processor(StampTimestamp).with_batch_exporter(
            LogExporter::builder()
                .with_tonic()
                .build()
                .expect("build OTLP log exporter"),
        );
        let exporter = MetricExporter::builder()
            .with_tonic()
            .build()
            .expect("build OTLP metric exporter");
        mp = mp.with_reader(
            PeriodicReader::builder(exporter)
                .with_interval(Duration::from_millis(interval))
                .build(),
        );
    }

    let tracer_provider = tp.build();
    let logger_provider = lp.build();
    let meter_provider = mp.build();

    global::set_text_map_propagator(TraceContextPropagator::new());
    global::set_tracer_provider(tracer_provider.clone());
    global::set_meter_provider(meter_provider.clone());

    // Spans only: log events are exported through the logs bridge, not as span events.
    let span_layer = tracing_opentelemetry::layer()
        .with_tracer(tracer_provider.tracer("app"))
        .with_location(false)
        .with_threads(false)
        .with_tracked_inactivity(false)
        .with_target(false)
        .with_filter(filter_fn(|m| m.is_span()).and(app_targets()));
    let log_layer = OpenTelemetryTracingBridge::new(&logger_provider).with_filter(app_targets());

    // try_init: a subscriber may already be set (e.g. by a test harness).
    if let Err(e) = tracing_subscriber::registry()
        .with(span_layer)
        .with(log_layer)
        .try_init()
    {
        eprintln!("telemetry: tracing subscriber not installed: {e}");
    }

    Telemetry {
        tracer_provider,
        meter_provider,
        logger_provider,
        exporting,
    }
}

impl Telemetry {
    /// Whether an OTLP exporter is configured.
    pub fn exporting(&self) -> bool {
        self.exporting
    }

    pub fn meter(&self, name: &'static str) -> Meter {
        use opentelemetry::metrics::MeterProvider as _;
        self.meter_provider.meter(name)
    }

    /// Blocking flush of traces, metrics and logs.
    pub fn force_flush_all(&self) {
        if let Err(e) = self.tracer_provider.force_flush() {
            eprintln!("telemetry: trace flush failed: {e}");
        }
        if let Err(e) = self.meter_provider.force_flush() {
            eprintln!("telemetry: metric flush failed: {e}");
        }
        if let Err(e) = self.logger_provider.force_flush() {
            eprintln!("telemetry: log flush failed: {e}");
        }
    }

    /// Flushes and shuts down all providers (blocking).
    pub fn shutdown(&self) {
        self.force_flush_all();
        let _ = self.tracer_provider.shutdown();
        let _ = self.meter_provider.shutdown();
        let _ = self.logger_provider.shutdown();
    }
}

/// The tracing bridge only sets the *observed* timestamp; backends such as ClickHouse key on
/// `Timestamp`, so copy it over when missing. Must be registered before the batch processor.
#[derive(Debug)]
struct StampTimestamp;

impl LogProcessor for StampTimestamp {
    fn emit(&self, record: &mut SdkLogRecord, _scope: &InstrumentationScope) {
        if record.timestamp().is_none() {
            let ts = record.observed_timestamp().unwrap_or_else(SystemTime::now);
            record.set_timestamp(ts);
        }
    }

    fn force_flush(&self) -> OTelSdkResult {
        Ok(())
    }

    fn shutdown_with_timeout(&self, _timeout: Duration) -> OTelSdkResult {
        Ok(())
    }
}

/// Gives every span the `test_run_id` of its parent when it has none of its own.
///
/// The hand-written root spans (web `http.request`, backend server span) and client spans set
/// the attribute explicitly. Spans created by `#[trace_all]` / `#[instrument(skip_all)]` cannot,
/// but verification counts spans per `test_run_id`, so they inherit it here. Parents start before
/// their children, so a parent's run id is always known when a child starts. Must be registered
/// before the exporting processor.
#[derive(Debug, Default)]
pub struct RunIdPropagator {
    /// Run id of every started, not yet ended span that has one.
    runs: Mutex<HashMap<SpanId, opentelemetry::Value>>,
}

impl SpanProcessor for RunIdPropagator {
    fn on_start(&self, span: &mut Span, cx: &Context) {
        let own = span.exported_data().and_then(|d| {
            d.attributes
                .into_iter()
                .find(|kv| kv.key.as_str() == RUN_ID_ATTR)
                .map(|kv| kv.value)
        });
        let mut runs = self.runs.lock().unwrap_or_else(|e| e.into_inner());
        let run = own.or_else(|| {
            let value = runs.get(&cx.span().span_context().span_id())?.clone();
            span.set_attribute(KeyValue::new(RUN_ID_ATTR, value.clone()));
            Some(value)
        });
        if let Some(run) = run {
            runs.insert(span.span_context().span_id(), run);
        }
    }

    fn on_end(&self, span: SpanData) {
        let mut runs = self.runs.lock().unwrap_or_else(|e| e.into_inner());
        runs.remove(&span.span_context.span_id());
    }

    fn force_flush(&self) -> OTelSdkResult {
        Ok(())
    }

    fn shutdown_with_timeout(&self, _timeout: Duration) -> OTelSdkResult {
        Ok(())
    }
}

struct MetadataInjector<'a>(&'a mut MetadataMap);

impl Injector for MetadataInjector<'_> {
    fn set(&mut self, key: &str, value: String) {
        if let (Ok(k), Ok(v)) = (
            MetadataKey::from_bytes(key.as_bytes()),
            MetadataValue::try_from(value.as_str()),
        ) {
            self.0.insert(k, v);
        }
    }
}

struct MetadataExtractor<'a>(&'a MetadataMap);

impl Extractor for MetadataExtractor<'_> {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.get(key).and_then(|v| v.to_str().ok())
    }

    fn keys(&self) -> Vec<&str> {
        self.0
            .keys()
            .map(|k| match k {
                tonic::metadata::KeyRef::Ascii(k) => k.as_str(),
                tonic::metadata::KeyRef::Binary(k) => k.as_str(),
            })
            .collect()
    }
}

/// Injects W3C trace context of `cx` into gRPC metadata.
pub fn inject_context(cx: &Context, metadata: &mut MetadataMap) {
    TraceContextPropagator::new().inject_context(cx, &mut MetadataInjector(metadata));
}

/// Extracts W3C trace context from gRPC metadata.
pub fn extract_context(metadata: &MetadataMap) -> Context {
    TraceContextPropagator::new().extract(&MetadataExtractor(metadata))
}

/// Convenience for building static-named attribute lists.
pub fn kv(key: &'static str, value: impl Into<opentelemetry::Value>) -> KeyValue {
    KeyValue::new(Cow::Borrowed(key), value.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use opentelemetry::trace::{
        SpanContext, SpanId, TraceContextExt, TraceFlags, TraceId, TraceState,
    };

    #[derive(Debug, Clone, Default)]
    struct Capture(std::sync::Arc<Mutex<Vec<SpanData>>>);

    impl SpanProcessor for Capture {
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

    #[test]
    fn child_spans_inherit_run_id() {
        let capture = Capture::default();
        let provider = SdkTracerProvider::builder()
            .with_span_processor(RunIdPropagator::default())
            .with_span_processor(capture.clone())
            .build();
        let subscriber = tracing_subscriber::registry()
            .with(tracing_opentelemetry::layer().with_tracer(provider.tracer("t")));
        tracing::subscriber::with_default(subscriber, || {
            let root = tracing::info_span!("root", test_run_id = "r1");
            let _root = root.enter();
            let child = tracing::info_span!("child");
            let _child = child.enter();
            let _grandchild = tracing::info_span!("grandchild").entered();
            let _own = tracing::info_span!("own", test_run_id = "r2").entered();
        });
        let spans = capture.0.lock().unwrap();
        let run_of = |name: &str| {
            let s = spans.iter().find(|s| s.name == name).unwrap();
            s.attributes
                .iter()
                .find(|kv| kv.key.as_str() == RUN_ID_ATTR)
                .map(|kv| kv.value.to_string())
        };
        assert_eq!(run_of("root").as_deref(), Some("r1"));
        assert_eq!(run_of("child").as_deref(), Some("r1"));
        assert_eq!(run_of("grandchild").as_deref(), Some("r1"));
        assert_eq!(run_of("own").as_deref(), Some("r2"));
    }

    #[test]
    fn context_round_trips_through_metadata() {
        let sc = SpanContext::new(
            TraceId::from(0x0123456789abcdef0123456789abcdef_u128),
            SpanId::from(0x1111111111111111_u64),
            TraceFlags::SAMPLED,
            true,
            TraceState::default(),
        );
        let cx = Context::new().with_remote_span_context(sc.clone());
        let mut md = MetadataMap::new();
        inject_context(&cx, &mut md);
        assert!(md.get("traceparent").is_some());
        let out = extract_context(&md);
        assert_eq!(out.span().span_context().trace_id(), sc.trace_id());
        assert_eq!(out.span().span_context().span_id(), sc.span_id());
    }
}
