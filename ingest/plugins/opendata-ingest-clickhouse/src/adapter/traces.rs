//! OTLP traces ClickHouse adapter: one row per span in a ClickStack-shaped
//! `otel_traces` table (see `stack/clickhouse/20_traces.sql`).

use serde::{Deserialize, Serialize};

use opendata_ingest_otel::logs::RowSourceCoordinates;
use opendata_ingest_otel::traces::DecodedSpanRecord;

use super::common::{
    AdapterKind, ChunkRecord, CommonAdapterConfig, PROVENANCE_COLUMNS, Route, plan_chunks,
    provenance_values,
};
use super::{Adapter, AdapterResult, ClickHouseAdapterBatch, InsertChunk, RowValue};

const KIND: AdapterKind = AdapterKind {
    tag: "otlp-traces-clickhouse-adapter:v1",
    signal: "traces",
};

impl ChunkRecord for DecodedSpanRecord {
    fn coords(&self) -> &RowSourceCoordinates {
        &self.source
    }
    fn approx_size_bytes(&self) -> usize {
        DecodedSpanRecord::approx_size_bytes(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TracesAdapterConfig {
    #[serde(flatten)]
    pub common: CommonAdapterConfig,
    pub table: String,
}

impl Default for TracesAdapterConfig {
    fn default() -> Self {
        Self {
            common: CommonAdapterConfig::default(),
            table: "otel_traces".to_string(),
        }
    }
}

/// Column order of every traces row (data columns, then provenance).
pub const COLUMNS: &[&str] = &[
    "Timestamp",
    "TraceId",
    "SpanId",
    "ParentSpanId",
    "TraceState",
    "SpanName",
    "SpanKind",
    "ServiceName",
    "ResourceAttributes",
    "ScopeName",
    "ScopeVersion",
    "SpanAttributes",
    "Duration",
    "StatusCode",
    "StatusMessage",
    "Events.Timestamp",
    "Events.Name",
    "Events.Attributes",
    "Links.TraceId",
    "Links.SpanId",
    "Links.TraceState",
    "Links.Attributes",
    PROVENANCE_COLUMNS[0],
    PROVENANCE_COLUMNS[1],
    PROVENANCE_COLUMNS[2],
    PROVENANCE_COLUMNS[3],
    PROVENANCE_COLUMNS[4],
    PROVENANCE_COLUMNS[5],
    PROVENANCE_COLUMNS[6],
];

#[derive(Debug, Clone)]
pub struct OtlpTracesClickHouseAdapter {
    config: TracesAdapterConfig,
}

impl OtlpTracesClickHouseAdapter {
    pub fn new(config: TracesAdapterConfig) -> Self {
        Self { config }
    }

    pub fn config(&self) -> &TracesAdapterConfig {
        &self.config
    }
}

impl Adapter for OtlpTracesClickHouseAdapter {
    type Input = DecodedSpanRecord;

    fn plan(&self, batch: ClickHouseAdapterBatch<Self::Input>) -> AdapterResult<Vec<InsertChunk>> {
        let ClickHouseAdapterBatch {
            identity, records, ..
        } = batch;
        let version = self.config.common.adapter_version;
        plan_chunks(
            &identity,
            records,
            &self.config.common,
            KIND,
            |_| Route {
                table: &self.config.table,
                columns: COLUMNS,
            },
            |r| span_row(r, version),
        )
    }
}

fn span_row(r: &DecodedSpanRecord, adapter_version: u32) -> Vec<RowValue> {
    let mut row = vec![
        RowValue::DateTime64Nanos(r.start_time_unix_nano),
        RowValue::String(r.trace_id_hex.clone()),
        RowValue::String(r.span_id_hex.clone()),
        RowValue::String(r.parent_span_id_hex.clone()),
        RowValue::String(r.trace_state.clone()),
        RowValue::LowCardinalityString(r.name.clone()),
        RowValue::LowCardinalityString(r.kind.to_string()),
        RowValue::LowCardinalityString(r.service_name.clone().unwrap_or_default()),
        RowValue::StringMap(r.resource_attributes.clone()),
        RowValue::String(r.scope_name.clone()),
        RowValue::String(r.scope_version.clone()),
        RowValue::StringMap(r.span_attributes.clone()),
        RowValue::UInt64(r.duration_nanos),
        RowValue::LowCardinalityString(r.status_code.to_string()),
        RowValue::String(r.status_message.clone()),
        RowValue::ArrayDateTime64Nanos(r.events.iter().map(|e| e.time_unix_nano).collect()),
        RowValue::ArrayString(r.events.iter().map(|e| e.name.clone()).collect()),
        RowValue::ArrayStringMap(r.events.iter().map(|e| e.attributes.clone()).collect()),
        RowValue::ArrayString(r.links.iter().map(|l| l.trace_id_hex.clone()).collect()),
        RowValue::ArrayString(r.links.iter().map(|l| l.span_id_hex.clone()).collect()),
        RowValue::ArrayString(r.links.iter().map(|l| l.trace_state.clone()).collect()),
        RowValue::ArrayStringMap(r.links.iter().map(|l| l.attributes.clone()).collect()),
    ];
    row.extend(provenance_values(&r.source, adapter_version));
    row
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;
    use opendata_ingest_otel::logs::RowSourceCoordinates;
    use opendata_ingest_otel::traces::{SpanEvent, SpanLink};
    use opendata_ingest_runtime::identity::{CommitIdentity, SchemaVersion, SequenceRange};
    use opendata_ingest_runtime::sink::SinkId;
    use opendata_ingest_runtime::source::SourceId;

    fn span(seq: u64, idx: u32) -> DecodedSpanRecord {
        DecodedSpanRecord {
            source: RowSourceCoordinates {
                buffer_sequence: seq,
                entry_index: 0,
                record_index: idx,
                manifest_path: "otel/traces/manifest".into(),
                data_path: "otel/traces/data/x".into(),
                ingestion_time_ms: 11,
            },
            start_time_unix_nano: 1_700_000_000_000_000_000,
            duration_nanos: 2_500,
            trace_id_hex: "a".repeat(32),
            span_id_hex: "b".repeat(16),
            parent_span_id_hex: String::new(),
            trace_state: String::new(),
            name: "GET /chain".into(),
            kind: "Server",
            service_name: Some("web".into()),
            resource_attributes: BTreeMap::from([("service.name".into(), "web".into())]),
            scope_name: "web".into(),
            scope_version: "0.1".into(),
            span_attributes: BTreeMap::from([("http.route".into(), "/chain".into())]),
            status_code: "Error",
            status_message: "boom".into(),
            events: vec![SpanEvent {
                time_unix_nano: 1_700_000_000_000_000_100,
                name: "exception".into(),
                attributes: BTreeMap::from([("exception.type".into(), "E".into())]),
            }],
            links: vec![SpanLink {
                trace_id_hex: "c".repeat(32),
                span_id_hex: "d".repeat(16),
                trace_state: String::new(),
                attributes: BTreeMap::new(),
            }],
        }
    }

    fn plan(records: Vec<DecodedSpanRecord>) -> Vec<InsertChunk> {
        let adapter = OtlpTracesClickHouseAdapter::new(TracesAdapterConfig::default());
        adapter
            .plan(ClickHouseAdapterBatch {
                identity: CommitIdentity {
                    source: SourceId::from("buffer"),
                    sink: SinkId::from("clickhouse_traces"),
                    range: SequenceRange::new(1, 1),
                    schema_version: SchemaVersion(1),
                },
                records,
                bytes: 0,
            })
            .expect("plan")
    }

    #[test]
    fn rows_align_with_columns_and_target_the_traces_table() {
        let chunks = plan(vec![span(1, 0), span(1, 1)]);
        assert_eq!(chunks.len(), 1);
        let c = &chunks[0];
        assert_eq!(c.table, "otel_traces");
        assert_eq!(c.database, "otel");
        assert_eq!(c.columns, COLUMNS);
        assert_eq!(c.rows.len(), 2);
        for row in &c.rows {
            assert_eq!(row.len(), COLUMNS.len());
        }
    }

    #[test]
    fn json_row_has_clickstack_column_names_and_values() {
        let chunk = &plan(vec![span(1, 0)])[0];
        let json: serde_json::Map<String, serde_json::Value> = COLUMNS
            .iter()
            .zip(&chunk.rows[0])
            .map(|(c, v)| (c.to_string(), v.to_json()))
            .collect();
        assert_eq!(json["Duration"], 2500);
        assert_eq!(json["SpanKind"], "Server");
        assert_eq!(json["StatusCode"], "Error");
        assert_eq!(json["ServiceName"], "web");
        assert_eq!(json["SpanName"], "GET /chain");
        assert_eq!(json["Events.Name"], serde_json::json!(["exception"]));
        assert_eq!(
            json["Events.Attributes"],
            serde_json::json!([{"exception.type": "E"}])
        );
        assert_eq!(json["Links.TraceId"], serde_json::json!(["c".repeat(32)]));
        assert_eq!(json["_odb_sequence"], 1);
        assert_eq!(json["_adapter_version"], 1);
        assert!(
            json["Timestamp"]
                .as_str()
                .unwrap()
                .starts_with("2023-11-14 22:13:20.")
        );
    }

    #[test]
    fn replay_produces_identical_tokens() {
        let a = plan(vec![span(1, 0), span(1, 1)]);
        let b = plan(vec![span(1, 1), span(1, 0)]);
        assert_eq!(a[0].idempotency_token, b[0].idempotency_token);
        assert!(
            a[0].idempotency_token
                .starts_with("otel/traces/manifest:otel.otel_traces:1-1:1:")
        );
    }

    #[test]
    fn span_without_events_or_links_yields_empty_arrays() {
        let mut s = span(1, 0);
        s.events.clear();
        s.links.clear();
        let chunk = &plan(vec![s])[0];
        let idx = COLUMNS.iter().position(|c| *c == "Events.Name").unwrap();
        assert_eq!(chunk.rows[0][idx].to_json(), serde_json::json!([]));
    }
}
