//! Figment-based configuration for the ingestor binary.
//!
//! YAML config file (typically mounted via ConfigMap) gets merged with
//! `INGESTOR__` env vars. Credentials only come from env so they never
//! land in the rendered ConfigMap.
//!
//! Layout (excerpt):
//!
//! ```yaml
//! buffer:
//!   manifest_path: ingest/otel/logs/manifest
//!   data_prefix:   ingest/otel/logs/data
//!   object_store:
//!     type: Aws
//!     region: us-west-2
//!     bucket: responsive-prod-opendata-otel-logs-us-west-2
//!
//! clickhouse:
//!   endpoint: https://example.clickhouse.cloud:8443
//!   database: responsive
//!   table: logs
//!   insert_quorum: auto
//!
//! runtime:
//!   dry_run: true
//!   poll_interval_ms: 250
//!   retry_max_attempts: 6
//!   retry_initial_backoff_ms: 100
//!
//! ack:
//!   policy: every_commit_group
//!
//! adapter:
//!   adapter_version: 1
//!   max_chunk_rows: 100000
//!   max_chunk_bytes: 33554432
//! ```
//!
//! Chunking thresholds live in the `adapter:` section — they are a
//! sink-internal concern. Older YAMLs that still set a top-level
//! `commit_group:` block parse cleanly; the section is ignored.

use std::path::Path;
use std::time::Duration;

use figment::Figment;
use figment::providers::{Env, Format, Yaml};
use serde::{Deserialize, Serialize};

use crate::adapter::common::CommonAdapterConfig;
use crate::adapter::logs::LogsAdapterConfig;
use crate::adapter::metrics::MetricsAdapterConfig;
use crate::adapter::traces::TracesAdapterConfig;
use crate::error::{IngestorError, IngestorResult};
use crate::writer::{HttpClientMode, WriterConfig};
use opendata_ingest_clickhouse::serializer::SerializationFormat;
use opendata_ingest_runtime::runtime::AckFlushPolicy;

/// Which OTLP signal this ingestor process consumes. The runtime drives one
/// decoder per Buffer manifest, so each signal runs as its own process with its
/// own `buffer.manifest_path`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalKind {
    #[default]
    Logs,
    Traces,
    Metrics,
}

impl SignalKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Logs => "logs",
            Self::Traces => "traces",
            Self::Metrics => "metrics",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IngestorConfig {
    #[serde(default)]
    pub signal: SignalKind,
    pub buffer: BufferSection,
    pub clickhouse: ClickHouseSection,
    pub runtime: RuntimeSection,
    pub ack: AckSection,
    pub adapter: AdapterSection,
    #[serde(default)]
    pub sink: SinkSection,
    #[serde(default)]
    pub metrics_server: MetricsServerSection,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricsServerSection {
    #[serde(default = "default_metrics_bind_addr")]
    pub bind_addr: String,
}

impl Default for MetricsServerSection {
    fn default() -> Self {
        Self {
            bind_addr: default_metrics_bind_addr(),
        }
    }
}

fn default_metrics_bind_addr() -> String {
    "0.0.0.0:9090".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BufferSection {
    pub manifest_path: String,
    pub data_prefix: String,
    pub object_store: common::ObjectStoreConfig,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClickHouseSection {
    pub endpoint: String,
    pub database: String,
    /// Destination table for logs / traces. Empty means the ClickStack default
    /// for the configured signal (`otel_logs` / `otel_traces`). Ignored for metrics.
    #[serde(default)]
    pub table: String,
    /// Destination tables for metrics (one per metric type).
    #[serde(default)]
    pub metrics_tables: MetricsTables,
    #[serde(default)]
    pub insert_quorum: Option<String>,
    /// Optional non-secret user; password and any secret user override
    /// come from env (`INGESTOR__CLICKHOUSE__USER`,
    /// `INGESTOR__CLICKHOUSE__PASSWORD`).
    #[serde(default = "default_user")]
    pub user: String,
    #[serde(default)]
    pub password: String,
}

fn default_user() -> String {
    "default".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricsTables {
    #[serde(default = "default_gauge_table")]
    pub gauge: String,
    #[serde(default = "default_sum_table")]
    pub sum: String,
    #[serde(default = "default_histogram_table")]
    pub histogram: String,
}

impl Default for MetricsTables {
    fn default() -> Self {
        Self {
            gauge: default_gauge_table(),
            sum: default_sum_table(),
            histogram: default_histogram_table(),
        }
    }
}

fn default_gauge_table() -> String {
    "otel_metrics_gauge".to_string()
}
fn default_sum_table() -> String {
    "otel_metrics_sum".to_string()
}
fn default_histogram_table() -> String {
    "otel_metrics_histogram".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeSection {
    #[serde(default = "default_dry_run")]
    pub dry_run: bool,
    #[serde(default = "default_poll_interval_ms")]
    pub poll_interval_ms: u64,
    #[serde(default = "default_retry_attempts")]
    pub retry_max_attempts: u32,
    #[serde(default = "default_retry_backoff_ms")]
    pub retry_initial_backoff_ms: u64,
    #[serde(default = "default_request_timeout_secs")]
    pub request_timeout_secs: u64,
    /// Per-source backpressure knobs. Defaults reproduce the
    /// library's pipelined profile (`fetch_concurrency = 8`,
    /// `decode_concurrency = 4`, `max_inflight_batches = 64`,
    /// `max_inflight_bytes = 256 MiB`, `estimated_max_batch_bytes =
    /// 4 MiB`, `oversize_fault_multiplier = 4`); an operator can
    /// override per-deployment via `INGESTOR__RUNTIME__*` env or YAML.
    #[serde(default = "default_max_inflight_batches")]
    pub max_inflight_batches: u32,
    #[serde(default = "default_max_inflight_bytes")]
    pub max_inflight_bytes: u64,
    #[serde(default = "default_estimated_max_batch_bytes")]
    pub estimated_max_batch_bytes: u64,
    #[serde(default = "default_fetch_concurrency")]
    pub fetch_concurrency: u32,
    #[serde(default = "default_decode_concurrency")]
    pub decode_concurrency: u32,
    #[serde(default = "default_oversize_fault_multiplier")]
    pub oversize_fault_multiplier: u32,
    /// Maximum descriptors requested per `next_descriptors` call in
    /// the admission arm. Default `8`; amortizes the per-cycle
    /// manifest GET across up to K descriptors.
    #[serde(default = "default_max_descriptors_per_poll")]
    pub max_descriptors_per_poll: usize,
}

fn default_dry_run() -> bool {
    true
}
fn default_poll_interval_ms() -> u64 {
    250
}
fn default_retry_attempts() -> u32 {
    6
}
fn default_retry_backoff_ms() -> u64 {
    100
}
fn default_request_timeout_secs() -> u64 {
    30
}
fn default_max_inflight_batches() -> u32 {
    64
}
fn default_max_inflight_bytes() -> u64 {
    256 * 1024 * 1024
}
fn default_estimated_max_batch_bytes() -> u64 {
    4 * 1024 * 1024
}
fn default_fetch_concurrency() -> u32 {
    8
}
fn default_decode_concurrency() -> u32 {
    4
}
fn default_oversize_fault_multiplier() -> u32 {
    4
}
fn default_max_descriptors_per_poll() -> usize {
    8
}
fn default_max_concurrent_commits() -> u32 {
    4
}

/// Sink-side knobs. The YAML `sink:` block is load-bearing:
/// `serialization_format` and `http_client_mode` are wired
/// end-to-end from here into the writer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SinkSection {
    /// Cap on concurrent in-flight `Sink::write` calls. The runtime
    /// applies a `.max(1)` floor, so 0 is treated as 1.
    #[serde(default = "default_max_concurrent_commits")]
    pub max_concurrent_commits: u32,
    /// Wire format the writer hands ClickHouse for each chunk.
    /// Default: `JsonEachRow`.
    #[serde(default)]
    pub serialization_format: SerializationFormat,
    /// HTTP client management mode. Default: `PerCall`; `Pooled`
    /// reuses a single client across calls.
    #[serde(default)]
    pub http_client_mode: HttpClientMode,
}

impl Default for SinkSection {
    fn default() -> Self {
        Self {
            max_concurrent_commits: default_max_concurrent_commits(),
            serialization_format: SerializationFormat::default(),
            http_client_mode: HttpClientMode::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AckSection {
    pub policy: AckPolicyKind,
    #[serde(default)]
    pub n: Option<u32>,
}

impl Default for AckSection {
    fn default() -> Self {
        Self {
            policy: AckPolicyKind::EveryCommitGroup,
            n: None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AckPolicyKind {
    EveryCommitGroup,
    EveryN,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AdapterSection {
    pub adapter_version: u32,
    pub max_chunk_rows: usize,
    pub max_chunk_bytes: usize,
    /// Whether to apply `insert_deduplication_token` on inserts. True
    /// for production replicated tables; set false for non-replicated
    /// targets (single-node dev/test).
    #[serde(default = "default_apply_dedup_token")]
    pub apply_deduplication_token: bool,
}

fn default_apply_dedup_token() -> bool {
    true
}

impl Default for AdapterSection {
    fn default() -> Self {
        Self {
            adapter_version: 1,
            max_chunk_rows: 100_000,
            max_chunk_bytes: 32 * 1024 * 1024,
            apply_deduplication_token: true,
        }
    }
}

impl IngestorConfig {
    /// Load config from a YAML file path, merging `INGESTOR__` env vars
    /// (with `__` as the section separator).
    pub fn load(path: impl AsRef<Path>) -> IngestorResult<Self> {
        let cfg: Self = Figment::new()
            .merge(Yaml::file(path))
            .merge(Env::prefixed("INGESTOR__").split("__"))
            .extract()
            .map_err(|e| IngestorError::Config(e.to_string()))?;
        Ok(cfg)
    }

    pub fn ack_flush_policy(&self) -> AckFlushPolicy {
        match self.ack.policy {
            AckPolicyKind::EveryCommitGroup => AckFlushPolicy::EveryCommitGroup,
            AckPolicyKind::EveryN => AckFlushPolicy::EveryN {
                n: self.ack.n.unwrap_or(1).max(1),
            },
        }
    }

    pub fn writer_config(&self) -> WriterConfig {
        WriterConfig {
            endpoint: self.clickhouse.endpoint.clone(),
            user: self.clickhouse.user.clone(),
            password: self.clickhouse.password.clone(),
            request_timeout: Duration::from_secs(self.runtime.request_timeout_secs),
            max_attempts: self.runtime.retry_max_attempts,
            initial_backoff: Duration::from_millis(self.runtime.retry_initial_backoff_ms),
            serialization_format: self.sink.serialization_format,
            http_client_mode: self.sink.http_client_mode.clone(),
        }
    }

    fn table_or(&self, default: &str) -> String {
        if self.clickhouse.table.is_empty() {
            default.to_string()
        } else {
            self.clickhouse.table.clone()
        }
    }

    pub fn logs_adapter_config(&self) -> LogsAdapterConfig {
        LogsAdapterConfig {
            database: self.clickhouse.database.clone(),
            table: self.table_or("otel_logs"),
            adapter_version: self.adapter.adapter_version,
            max_chunk_rows: self.adapter.max_chunk_rows,
            max_chunk_bytes: self.adapter.max_chunk_bytes,
            insert_quorum: self.clickhouse.insert_quorum.clone(),
            apply_deduplication_token: self.adapter.apply_deduplication_token,
        }
    }

    fn common_adapter_config(&self) -> CommonAdapterConfig {
        CommonAdapterConfig {
            database: self.clickhouse.database.clone(),
            adapter_version: self.adapter.adapter_version,
            max_chunk_rows: self.adapter.max_chunk_rows,
            max_chunk_bytes: self.adapter.max_chunk_bytes,
            insert_quorum: self.clickhouse.insert_quorum.clone(),
            apply_deduplication_token: self.adapter.apply_deduplication_token,
        }
    }

    pub fn traces_adapter_config(&self) -> TracesAdapterConfig {
        TracesAdapterConfig {
            common: self.common_adapter_config(),
            table: self.table_or("otel_traces"),
        }
    }

    pub fn metrics_adapter_config(&self) -> MetricsAdapterConfig {
        MetricsAdapterConfig {
            common: self.common_adapter_config(),
            gauge_table: self.clickhouse.metrics_tables.gauge.clone(),
            sum_table: self.clickhouse.metrics_tables.sum.clone(),
            histogram_table: self.clickhouse.metrics_tables.histogram.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_render_via_serde() {
        // A `commit_group:` block from older YAMLs is parsed and
        // silently ignored — chunking thresholds live under `adapter:`.
        let yaml = r#"
buffer:
  manifest_path: ingest/otel/logs/manifest
  data_prefix: ingest/otel/logs/data
  object_store:
    type: InMemory

clickhouse:
  endpoint: http://localhost:8123
  database: responsive
  table: logs

runtime:
  dry_run: true
  poll_interval_ms: 250
  retry_max_attempts: 6
  retry_initial_backoff_ms: 100
  request_timeout_secs: 30

commit_group:
  max_rows: 100000
  max_bytes: 33554432
  max_age_ms: 1000

ack:
  policy: every_commit_group

adapter:
  adapter_version: 1
  max_chunk_rows: 100000
  max_chunk_bytes: 33554432
"#;
        let cfg: IngestorConfig = serde_yaml::from_str(yaml).expect("parse");

        let writer = cfg.writer_config();
        assert_eq!(writer.endpoint, "http://localhost:8123");
        assert_eq!(writer.max_attempts, 6);
        // SinkSection is absent in this YAML, so serde(default) on
        // `IngestorConfig.sink` produces SinkSection::default(), and
        // `writer_config()` propagates the enum defaults to the
        // writer (JsonEachRow + PerCall). The bench harness's
        // production-shaped defaults match these.
        assert_eq!(
            writer.serialization_format,
            SerializationFormat::JsonEachRow
        );
        assert_eq!(writer.http_client_mode, HttpClientMode::PerCall);
        assert_eq!(cfg.sink.max_concurrent_commits, 4);

        match cfg.ack_flush_policy() {
            AckFlushPolicy::EveryCommitGroup => {}
            other => panic!("unexpected policy: {other:?}"),
        }

        let adapter = cfg.logs_adapter_config();
        assert_eq!(adapter.database, "responsive");
        assert_eq!(adapter.table, "logs");
        assert_eq!(adapter.adapter_version, 1);

        // Multi-descriptor admission knob default; the YAML above
        // does not set it, so the default (8) must come through and
        // match the runtime crate's own default (also 8).
        assert_eq!(cfg.runtime.max_descriptors_per_poll, 8);
    }

    /// Pin that an explicit YAML override for
    /// `runtime.max_descriptors_per_poll` reaches the parsed
    /// `RuntimeSection`. Catches a serde-rename or default-only
    /// regression in the end-to-end YAML→ingestor wiring.
    #[test]
    fn runtime_max_descriptors_per_poll_override_parses() {
        let yaml = r#"
buffer:
  manifest_path: m
  data_prefix: d
  object_store:
    type: InMemory
clickhouse:
  endpoint: http://x:8123
  database: db
  table: t
runtime:
  max_descriptors_per_poll: 16
ack:
  policy: every_commit_group
adapter:
  adapter_version: 1
  max_chunk_rows: 1
  max_chunk_bytes: 1
"#;
        let cfg: IngestorConfig = serde_yaml::from_str(yaml).expect("parse");
        assert_eq!(cfg.runtime.max_descriptors_per_poll, 16);
    }

    /// Wiring sanity: sink.serialization_format,
    /// sink.http_client_mode, and sink.max_concurrent_commits are
    /// load-bearing — values set in the YAML `sink:` block reach the
    /// writer rather than being overridden by a default.
    #[test]
    fn sink_section_wires_serialization_format_and_http_client_mode() {
        let yaml = r#"
buffer:
  manifest_path: m
  data_prefix: d
  object_store:
    type: InMemory
clickhouse:
  endpoint: http://x:8123
  database: db
  table: t
runtime:
  dry_run: true
  poll_interval_ms: 250
  retry_max_attempts: 6
  retry_initial_backoff_ms: 100
  request_timeout_secs: 30
ack:
  policy: every_commit_group
adapter:
  adapter_version: 1
  max_chunk_rows: 1
  max_chunk_bytes: 1
sink:
  max_concurrent_commits: 16
  serialization_format: row_binary
  http_client_mode:
    mode: pooled
    pool_max_idle_per_host: 32
    pool_idle_timeout_ms: 30000
"#;
        let cfg: IngestorConfig = serde_yaml::from_str(yaml).expect("parse");
        assert_eq!(cfg.sink.max_concurrent_commits, 16);
        assert_eq!(
            cfg.sink.serialization_format,
            SerializationFormat::RowBinary
        );
        match &cfg.sink.http_client_mode {
            HttpClientMode::Pooled {
                pool_max_idle_per_host,
                pool_idle_timeout_ms,
            } => {
                assert_eq!(*pool_max_idle_per_host, 32);
                assert_eq!(*pool_idle_timeout_ms, 30000);
            }
            other => panic!("expected Pooled, got {other:?}"),
        }

        let writer = cfg.writer_config();
        assert_eq!(writer.serialization_format, SerializationFormat::RowBinary);
        match &writer.http_client_mode {
            HttpClientMode::Pooled {
                pool_max_idle_per_host,
                pool_idle_timeout_ms,
            } => {
                assert_eq!(*pool_max_idle_per_host, 32);
                assert_eq!(*pool_idle_timeout_ms, 30000);
            }
            other => panic!("expected Pooled, got {other:?}"),
        }
    }

    #[test]
    fn metrics_server_section_defaults_when_absent() {
        // YAML config files in production omit the metrics_server
        // section; the binary relies on serde(default) producing the
        // canonical bind address. A regression here would silently
        // change the production listen port.
        let yaml = r#"
buffer:
  manifest_path: m
  data_prefix: d
  object_store:
    type: InMemory
clickhouse:
  endpoint: http://x:8123
  database: db
  table: t
runtime:
  dry_run: true
  poll_interval_ms: 250
  retry_max_attempts: 6
  retry_initial_backoff_ms: 100
  request_timeout_secs: 30
commit_group:
  max_rows: 1
  max_bytes: 1
  max_age_ms: 1
ack:
  policy: every_commit_group
adapter:
  adapter_version: 1
  max_chunk_rows: 1
  max_chunk_bytes: 1
"#;
        let cfg: IngestorConfig = serde_yaml::from_str(yaml).expect("parse");
        assert_eq!(cfg.metrics_server.bind_addr, "0.0.0.0:9090");
    }

    #[test]
    fn metrics_server_section_overrides_bind_addr() {
        let yaml = r#"
buffer:
  manifest_path: m
  data_prefix: d
  object_store:
    type: InMemory
clickhouse:
  endpoint: http://x:8123
  database: db
  table: t
runtime:
  dry_run: true
  poll_interval_ms: 250
  retry_max_attempts: 6
  retry_initial_backoff_ms: 100
  request_timeout_secs: 30
commit_group:
  max_rows: 1
  max_bytes: 1
  max_age_ms: 1
ack:
  policy: every_commit_group
adapter:
  adapter_version: 1
  max_chunk_rows: 1
  max_chunk_bytes: 1
metrics_server:
  bind_addr: 127.0.0.1:8080
"#;
        let cfg: IngestorConfig = serde_yaml::from_str(yaml).expect("parse");
        assert_eq!(cfg.metrics_server.bind_addr, "127.0.0.1:8080");
    }

    #[test]
    fn ack_every_n_clamps_zero_to_one() {
        let yaml = r#"
buffer:
  manifest_path: m
  data_prefix: d
  object_store:
    type: InMemory
clickhouse:
  endpoint: http://x:8123
  database: db
  table: t
runtime:
  dry_run: true
  poll_interval_ms: 250
  retry_max_attempts: 6
  retry_initial_backoff_ms: 100
  request_timeout_secs: 30
commit_group:
  max_rows: 1
  max_bytes: 1
  max_age_ms: 1
ack:
  policy: every_n
  n: 0
adapter:
  adapter_version: 1
  max_chunk_rows: 1
  max_chunk_bytes: 1
"#;
        let cfg: IngestorConfig = serde_yaml::from_str(yaml).expect("parse");
        match cfg.ack_flush_policy() {
            AckFlushPolicy::EveryN { n } => assert_eq!(n, 1),
            other => panic!("unexpected: {other:?}"),
        }
    }

    const BASE_YAML: &str = r#"
buffer:
  manifest_path: otel/x/manifest
  data_prefix: otel/x/data
  object_store:
    type: InMemory
clickhouse:
  endpoint: http://localhost:8123
  database: otel
runtime: {}
ack:
  policy: every_commit_group
adapter:
  adapter_version: 1
  max_chunk_rows: 10
  max_chunk_bytes: 1000
"#;

    fn parse(extra: &str) -> IngestorConfig {
        serde_yaml::from_str(&format!("{BASE_YAML}{extra}")).expect("parse config")
    }

    #[test]
    fn signal_defaults_to_logs_and_tables_to_clickstack_names() {
        let cfg = parse("");
        assert_eq!(cfg.signal, SignalKind::Logs);
        assert_eq!(cfg.logs_adapter_config().table, "otel_logs");
        assert_eq!(cfg.traces_adapter_config().table, "otel_traces");
        let m = cfg.metrics_adapter_config();
        assert_eq!(
            (
                m.gauge_table.as_str(),
                m.sum_table.as_str(),
                m.histogram_table.as_str()
            ),
            (
                "otel_metrics_gauge",
                "otel_metrics_sum",
                "otel_metrics_histogram"
            )
        );
    }

    #[test]
    fn signal_and_table_overrides_flow_into_adapter_configs() {
        let cfg: IngestorConfig = serde_yaml::from_str(
            &BASE_YAML
                .replace(
                    "  database: otel\n",
                    "  database: otel\n  table: custom_spans\n",
                )
                .replace("buffer:\n", "signal: traces\nbuffer:\n"),
        )
        .expect("parse");
        assert_eq!(cfg.signal, SignalKind::Traces);
        let t = cfg.traces_adapter_config();
        assert_eq!(t.table, "custom_spans");
        assert_eq!(t.common.database, "otel");
        assert_eq!(t.common.max_chunk_rows, 10);
        assert_eq!(t.common.max_chunk_bytes, 1000);
    }

    #[test]
    fn unknown_signal_is_rejected() {
        let r: Result<IngestorConfig, _> =
            serde_yaml::from_str(&BASE_YAML.replace("buffer:\n", "signal: profiles\nbuffer:\n"));
        assert!(r.is_err());
    }
}
