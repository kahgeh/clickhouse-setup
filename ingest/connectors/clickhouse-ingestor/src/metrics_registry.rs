//! Dual-backend metrics plumbing: the bin owns one
//! `prometheus_client::registry::Registry` and one
//! `metrics_exporter_prometheus::PrometheusHandle`, registers the
//! runtime + plugin metric structs against the Registry, and serves
//! both backends from `/metrics`.
//!
//! The Registry owns the typed `RuntimeMetrics` + `ClickHouseMetrics`
//! families; every runtime + plugin emission routes through
//! `Arc<RuntimeMetrics>` / `Arc<ClickHouseMetrics>`. The
//! `PrometheusHandle` renders the buffer crate's `metrics-rs`
//! emissions, which are the only counters that flow through the
//! `metrics-rs` path.

use std::sync::Arc;

use anyhow::Result;
use metrics_exporter_prometheus::PrometheusHandle;
use opendata_ingest_clickhouse::metrics::ClickHouseMetrics;
use opendata_ingest_otel::common::DecodeCounters;
use opendata_ingest_runtime::metrics::RuntimeMetrics;
use prometheus_client::collector::Collector;
use prometheus_client::encoding::{DescriptorEncoder, EncodeMetric};
use prometheus_client::metrics::counter::ConstCounter;
use prometheus_client::registry::Registry;

/// Publishes a decoder's drop counters at scrape time. Silent drops would
/// defeat the point of skipping instead of failing, so they are first-class
/// metrics: `ingestor_decode_malformed_entries_total` and
/// `ingestor_decode_skipped_points_total`, both labelled by `signal`.
#[derive(Debug)]
struct DecodeCountersCollector {
    signal: &'static str,
    counters: Arc<DecodeCounters>,
}

impl Collector for DecodeCountersCollector {
    fn encode(&self, mut encoder: DescriptorEncoder) -> Result<(), std::fmt::Error> {
        let labels = [("signal", self.signal)];
        for (name, help, value) in [
            (
                "ingestor_decode_malformed_entries",
                "Buffer entries that were not valid OTLP protobuf and were skipped",
                self.counters.malformed_entries(),
            ),
            (
                "ingestor_decode_skipped_points",
                "Data points deliberately not stored (unsupported metric types)",
                self.counters.skipped_points(),
            ),
        ] {
            let counter = ConstCounter::new(value);
            let mut family = encoder.encode_descriptor(name, help, None, counter.metric_type())?;
            counter.encode(family.encode_family(&labels)?)?;
        }
        Ok(())
    }
}

/// Owns the prometheus-client Registry plus the per-component metric
/// structs and the `metrics-rs` PrometheusHandle. Constructed
/// once in `main` after `set_global_recorder`; passed by value to
/// `metrics_server::serve` and shared with the runtime + sink
/// constructors via the Arc fields.
pub struct MetricsRegistry {
    pub registry: Registry,
    pub runtime: Arc<RuntimeMetrics>,
    pub clickhouse: Arc<ClickHouseMetrics>,
    pub prom_handle: PrometheusHandle,
}

impl MetricsRegistry {
    /// Build a fresh Registry, register both metric structs, and pair
    /// them with the supplied `PrometheusHandle` (which is what the
    /// buffer-crate's `metrics-rs` emission renders through).
    pub fn new(prom_handle: PrometheusHandle) -> Result<Self> {
        let mut registry = Registry::default();
        let runtime = Arc::new(RuntimeMetrics::new());
        let clickhouse = Arc::new(ClickHouseMetrics::new());
        runtime.register(&mut registry);
        clickhouse.register(&mut registry);
        Ok(Self {
            registry,
            runtime,
            clickhouse,
            prom_handle,
        })
    }
}

impl MetricsRegistry {
    /// Expose `counters` (from the decoder for `signal`) on `/metrics`.
    pub fn register_decode_counters(
        &mut self,
        signal: &'static str,
        counters: Arc<DecodeCounters>,
    ) {
        self.registry
            .register_collector(Box::new(DecodeCountersCollector { signal, counters }));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use metrics_exporter_prometheus::PrometheusBuilder;

    #[test]
    fn decode_counters_are_exposed_with_signal_label() {
        let recorder = PrometheusBuilder::new().build_recorder();
        let mut r = MetricsRegistry::new(recorder.handle()).expect("build registry");
        let decoder = opendata_ingest_otel::traces::OtlpTracesDecoder::new();
        let counters = decoder.counters();
        r.register_decode_counters("traces", Arc::clone(&counters));

        // Drive the counter through the real decoder, not by poking internals.
        use opendata_ingest_runtime::decoder::Decoder;
        use opendata_ingest_runtime::source::{SourceBatch, SourceEntry};
        let batch = SourceBatch {
            source: "buffer".into(),
            sequence: 1,
            manifest_path: "m".into(),
            data_object_path: "d".into(),
            entries: vec![SourceEntry {
                entry_index: 0,
                raw_bytes: bytes::Bytes::from_static(b"not protobuf"),
                raw_metadata: bytes::Bytes::from_static(&[1, 3, 1, 0]),
                ingestion_time_ms: 0,
            }],
        };
        decoder.decode(batch).expect("malformed entry is not fatal");

        let mut buf = String::new();
        prometheus_client::encoding::text::encode(&mut buf, &r.registry).expect("encode");
        assert!(
            buf.contains("ingestor_decode_malformed_entries_total{signal=\"traces\"} 1"),
            "missing malformed counter:\n{buf}"
        );
        assert!(
            buf.contains("ingestor_decode_skipped_points_total{signal=\"traces\"} 0"),
            "missing skipped counter:\n{buf}"
        );
    }
    use prometheus_client::encoding::text::encode;

    #[test]
    fn registry_renders_runtime_and_clickhouse_metric_names() {
        let recorder = PrometheusBuilder::new().build_recorder();
        let handle = recorder.handle();
        let r = MetricsRegistry::new(handle).expect("build registry");

        // Touch one metric from each backend so encode emits a series
        // (TYPE lines render even on empty Families, but a real series
        // lets us assert the round trip is wired correctly).
        r.runtime
            .descriptors_handed_out
            .get_or_create(&opendata_ingest_runtime::metrics::SourceLabels {
                source: "buffer".into(),
            })
            .inc();
        r.clickhouse.commit_bytes.inc_by(1024);

        let mut buf = String::new();
        encode(&mut buf, &r.registry).expect("encode");
        assert!(
            buf.contains("runtime_descriptors_handed_out_total{source=\"buffer\"} 1"),
            "expected runtime metric in registry output:\n{buf}",
        );
        assert!(
            buf.contains("ingestor_commit_bytes_total 1024"),
            "expected clickhouse metric in registry output:\n{buf}",
        );
    }
}
