//! OTLP traces decoder.
//!
//! Flattens `ResourceSpans -> ScopeSpans -> Span` into one [`DecodedSpanRecord`]
//! per span. Differences from the logs decoder, on purpose:
//!
//! * a payload that is not valid OTLP protobuf is **counted and skipped** (the
//!   entry yields zero records and the source range is still acknowledged)
//!   instead of failing the whole pipeline on a poison pill. An envelope that
//!   names the wrong signal stays fatal, because that is a routing bug.
//! * kind/status are rendered as the strings the ClickStack `otel_traces`
//!   schema uses (`Server`, `Error`, ...).

use std::any::Any;
use std::collections::BTreeMap;
use std::sync::Arc;

use opentelemetry_proto::tonic::collector::trace::v1::ExportTraceServiceRequest;
use opentelemetry_proto::tonic::trace::v1::Span;
use prost::Message;

use opendata_ingest_runtime::decoded_batch::{DecodedBatch, TypedRecords, TypedSchema};
use opendata_ingest_runtime::decoder::Decoder;
use opendata_ingest_runtime::error::RuntimeResult;
use opendata_ingest_runtime::identity::SchemaVersion;
use opendata_ingest_runtime::source::SourceBatch;

use crate::common::{
    DecodeCounters, assemble_batch, attrs_to_map, map_bytes, resource_info, row_coords, scope_info,
    validate_envelopes,
};
use crate::envelope::{ConfiguredEnvelope, PayloadEncoding, SignalType};
use crate::logs::RowSourceCoordinates;

pub const OTLP_TRACES_SCHEMA_NAME: &str = "opendata.otel.traces.v1";

#[derive(Debug, Clone, PartialEq)]
pub struct SpanEvent {
    pub time_unix_nano: u64,
    pub name: String,
    pub attributes: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SpanLink {
    pub trace_id_hex: String,
    pub span_id_hex: String,
    pub trace_state: String,
    pub attributes: BTreeMap<String, String>,
}

/// One flattened span with its Buffer source coordinates.
#[derive(Debug, Clone)]
pub struct DecodedSpanRecord {
    pub source: RowSourceCoordinates,
    pub start_time_unix_nano: u64,
    pub duration_nanos: u64,
    pub trace_id_hex: String,
    pub span_id_hex: String,
    pub parent_span_id_hex: String,
    pub trace_state: String,
    pub name: String,
    /// `Unspecified | Internal | Server | Client | Producer | Consumer`.
    pub kind: &'static str,
    pub service_name: Option<String>,
    pub resource_attributes: BTreeMap<String, String>,
    pub scope_name: String,
    pub scope_version: String,
    pub span_attributes: BTreeMap<String, String>,
    /// `Unset | Ok | Error`.
    pub status_code: &'static str,
    pub status_message: String,
    pub events: Vec<SpanEvent>,
    pub links: Vec<SpanLink>,
}

impl DecodedSpanRecord {
    pub fn approx_size_bytes(&self) -> usize {
        let mut sz = std::mem::size_of::<Self>();
        sz += self.trace_id_hex.len() + self.span_id_hex.len() + self.parent_span_id_hex.len();
        sz += self.trace_state.len() + self.name.len() + self.status_message.len();
        sz += self.service_name.as_ref().map_or(0, |s| s.len());
        sz += self.scope_name.len() + self.scope_version.len();
        sz += map_bytes(&self.resource_attributes) + map_bytes(&self.span_attributes);
        for e in &self.events {
            sz += 8 + e.name.len() + map_bytes(&e.attributes);
        }
        for l in &self.links {
            sz += l.trace_id_hex.len() + l.span_id_hex.len() + l.trace_state.len();
            sz += map_bytes(&l.attributes);
        }
        sz
    }
}

pub(crate) fn span_kind_str(kind: i32) -> &'static str {
    match kind {
        1 => "Internal",
        2 => "Server",
        3 => "Client",
        4 => "Producer",
        5 => "Consumer",
        _ => "Unspecified",
    }
}

pub(crate) fn status_code_str(code: i32) -> &'static str {
    match code {
        1 => "Ok",
        2 => "Error",
        _ => "Unset",
    }
}

/// `TypedRecords` carrier for decoded spans.
#[derive(Debug)]
pub struct TypedDecodedTraces {
    records: Vec<DecodedSpanRecord>,
    schema: TypedSchema,
}

impl TypedDecodedTraces {
    pub fn new(records: Vec<DecodedSpanRecord>) -> Self {
        Self {
            records,
            schema: TypedSchema {
                name: OTLP_TRACES_SCHEMA_NAME.into(),
                version: SchemaVersion(1),
            },
        }
    }

    pub fn records(&self) -> &[DecodedSpanRecord] {
        &self.records
    }
}

impl TypedRecords for TypedDecodedTraces {
    fn record_count(&self) -> usize {
        self.records.len()
    }

    fn estimated_bytes(&self) -> usize {
        self.records.iter().map(|r| r.approx_size_bytes()).sum()
    }

    fn schema(&self) -> &TypedSchema {
        &self.schema
    }

    fn as_any(&self) -> &dyn Any {
        self
    }
}

#[derive(Debug)]
pub struct OtlpTracesDecoder {
    expected: ConfiguredEnvelope,
    counters: Arc<DecodeCounters>,
}

impl Default for OtlpTracesDecoder {
    fn default() -> Self {
        Self::new()
    }
}

impl OtlpTracesDecoder {
    pub fn new() -> Self {
        Self {
            expected: ConfiguredEnvelope {
                version: 1,
                signal_type: SignalType::Traces,
                encoding: PayloadEncoding::OtlpProtobuf,
            },
            counters: Arc::new(DecodeCounters::default()),
        }
    }

    pub fn counters(&self) -> Arc<DecodeCounters> {
        Arc::clone(&self.counters)
    }

    /// Decode every entry in `batch`; malformed entries are counted and skipped.
    pub fn decode_traces(&self, batch: &SourceBatch) -> Vec<DecodedSpanRecord> {
        let mut records = Vec::new();
        for entry in &batch.entries {
            let req = match ExportTraceServiceRequest::decode(entry.raw_bytes.as_ref()) {
                Ok(req) => req,
                Err(e) => {
                    self.counters.record_malformed();
                    tracing::warn!(
                        sequence = batch.sequence,
                        entry_index = entry.entry_index,
                        error = %e,
                        "skipping malformed OTLP traces entry"
                    );
                    continue;
                }
            };
            let mut record_index: u32 = 0;
            for rs in &req.resource_spans {
                let res = resource_info(&rs.resource, &rs.schema_url);
                for ss in &rs.scope_spans {
                    let scope = scope_info(&ss.scope, &ss.schema_url);
                    for span in &ss.spans {
                        records.push(flatten_span(
                            span,
                            row_coords(batch, entry, record_index),
                            &res.service_name,
                            &res.attributes,
                            &scope.name,
                            &scope.version,
                        ));
                        record_index += 1;
                    }
                }
            }
        }
        records
    }
}

fn flatten_span(
    span: &Span,
    source: RowSourceCoordinates,
    service_name: &Option<String>,
    resource_attributes: &BTreeMap<String, String>,
    scope_name: &str,
    scope_version: &str,
) -> DecodedSpanRecord {
    let (status_code, status_message) = span
        .status
        .as_ref()
        .map(|s| (status_code_str(s.code), s.message.clone()))
        .unwrap_or(("Unset", String::new()));
    DecodedSpanRecord {
        source,
        start_time_unix_nano: span.start_time_unix_nano,
        duration_nanos: span
            .end_time_unix_nano
            .saturating_sub(span.start_time_unix_nano),
        trace_id_hex: hex::encode(&span.trace_id),
        span_id_hex: hex::encode(&span.span_id),
        parent_span_id_hex: hex::encode(&span.parent_span_id),
        trace_state: span.trace_state.clone(),
        name: span.name.clone(),
        kind: span_kind_str(span.kind),
        service_name: service_name.clone(),
        resource_attributes: resource_attributes.clone(),
        scope_name: scope_name.to_string(),
        scope_version: scope_version.to_string(),
        span_attributes: attrs_to_map(&span.attributes),
        status_code,
        status_message,
        events: span
            .events
            .iter()
            .map(|e| SpanEvent {
                time_unix_nano: e.time_unix_nano,
                name: e.name.clone(),
                attributes: attrs_to_map(&e.attributes),
            })
            .collect(),
        links: span
            .links
            .iter()
            .map(|l| SpanLink {
                trace_id_hex: hex::encode(&l.trace_id),
                span_id_hex: hex::encode(&l.span_id),
                trace_state: l.trace_state.clone(),
                attributes: attrs_to_map(&l.attributes),
            })
            .collect(),
    }
}

impl Decoder for OtlpTracesDecoder {
    fn accepts(&self, raw_metadata: &[u8]) -> bool {
        crate::envelope::decode_one(0, raw_metadata)
            .map(|env| self.expected.matches(&env))
            .unwrap_or(false)
    }

    fn decode(&self, batch: SourceBatch) -> RuntimeResult<Vec<DecodedBatch>> {
        validate_envelopes(&batch, &self.expected)?;
        let records = self.decode_traces(&batch);
        let estimate: u64 = records.iter().map(|r| r.approx_size_bytes() as u64).sum();
        let coords: Vec<RowSourceCoordinates> = records.iter().map(|r| r.source.clone()).collect();
        let typed = Arc::new(TypedDecodedTraces::new(records));
        Ok(vec![assemble_batch(&batch, coords.iter(), estimate, typed)])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bytes::Bytes;
    use opentelemetry_proto::tonic::common::v1::{
        AnyValue, InstrumentationScope, KeyValue, any_value::Value,
    };
    use opentelemetry_proto::tonic::resource::v1::Resource;
    use opentelemetry_proto::tonic::trace::v1::{
        ResourceSpans, ScopeSpans, Status, span::Event, span::Link,
    };

    fn kv(k: &str, v: &str) -> KeyValue {
        KeyValue {
            key: k.into(),
            value: Some(AnyValue {
                value: Some(Value::StringValue(v.into())),
            }),
        }
    }

    fn span(name: &str, kind: i32, code: i32, start: u64, end: u64) -> Span {
        Span {
            trace_id: vec![0xaa; 16],
            span_id: vec![0xbb; 8],
            parent_span_id: vec![],
            name: name.into(),
            kind,
            start_time_unix_nano: start,
            end_time_unix_nano: end,
            attributes: vec![kv("http.route", "/x")],
            status: Some(Status {
                message: if code == 2 {
                    "boom".into()
                } else {
                    String::new()
                },
                code,
            }),
            ..Default::default()
        }
    }

    fn request(spans_per_resource: &[Vec<Span>]) -> ExportTraceServiceRequest {
        ExportTraceServiceRequest {
            resource_spans: spans_per_resource
                .iter()
                .enumerate()
                .map(|(i, spans)| ResourceSpans {
                    resource: Some(Resource {
                        attributes: vec![kv("service.name", &format!("svc-{i}"))],
                        dropped_attributes_count: 0,
                    }),
                    scope_spans: vec![ScopeSpans {
                        scope: Some(InstrumentationScope {
                            name: "scope".into(),
                            version: "1.2".into(),
                            attributes: vec![],
                            dropped_attributes_count: 0,
                        }),
                        spans: spans.clone(),
                        schema_url: String::new(),
                    }],
                    schema_url: String::new(),
                })
                .collect(),
        }
    }

    fn batch(payloads: Vec<Vec<u8>>, metadata: &'static [u8]) -> SourceBatch {
        use opendata_ingest_runtime::source::SourceEntry;
        SourceBatch {
            source: "buffer".into(),
            sequence: 7,
            manifest_path: "otel/traces/manifest".into(),
            data_object_path: "otel/traces/data/x".into(),
            entries: payloads
                .into_iter()
                .enumerate()
                .map(|(i, p)| SourceEntry {
                    entry_index: i as u32,
                    raw_bytes: Bytes::from(p),
                    raw_metadata: Bytes::from_static(metadata),
                    ingestion_time_ms: 5,
                })
                .collect(),
        }
    }

    const TRACES_META: &[u8] = &[1, 3, 1, 0];
    const LOGS_META: &[u8] = &[1, 2, 1, 0];

    #[test]
    fn flattens_tree_and_assigns_record_index() {
        let req = request(&[
            vec![span("a", 2, 0, 10, 30), span("b", 3, 0, 12, 20)],
            vec![span("c", 1, 0, 5, 6)],
        ]);
        let b = batch(vec![req.encode_to_vec()], TRACES_META);
        let d = OtlpTracesDecoder::new();
        let recs = d.decode_traces(&b);
        assert_eq!(recs.len(), 3);
        assert_eq!(
            recs.iter()
                .map(|r| r.source.record_index)
                .collect::<Vec<_>>(),
            vec![0, 1, 2]
        );
        assert_eq!(recs[0].service_name.as_deref(), Some("svc-0"));
        assert_eq!(recs[2].service_name.as_deref(), Some("svc-1"));
        assert_eq!(recs[0].scope_name, "scope");
        assert_eq!(recs[0].scope_version, "1.2");
        for r in &recs {
            assert_eq!(r.source.buffer_sequence, 7);
            assert_eq!(r.source.manifest_path, "otel/traces/manifest");
            assert_eq!(r.source.ingestion_time_ms, 5);
        }
    }

    #[test]
    fn maps_kind_status_duration_and_ids() {
        let req = request(&[vec![
            span("server-err", 2, 2, 100, 350),
            span("client-ok", 3, 1, 100, 101),
            span("internal-unset", 1, 0, 100, 100),
        ]]);
        let recs =
            OtlpTracesDecoder::new().decode_traces(&batch(vec![req.encode_to_vec()], TRACES_META));
        assert_eq!((recs[0].kind, recs[0].status_code), ("Server", "Error"));
        assert_eq!(recs[0].status_message, "boom");
        assert_eq!(recs[0].duration_nanos, 250);
        assert_eq!((recs[1].kind, recs[1].status_code), ("Client", "Ok"));
        assert_eq!((recs[2].kind, recs[2].status_code), ("Internal", "Unset"));
        assert_eq!(recs[0].trace_id_hex, "a".repeat(32));
        assert_eq!(recs[0].span_id_hex, "b".repeat(16));
        assert_eq!(recs[0].parent_span_id_hex, "", "root span has empty parent");
        assert_eq!(
            recs[0]
                .span_attributes
                .get("http.route")
                .map(String::as_str),
            Some("/x")
        );
    }

    #[test]
    fn end_before_start_saturates_to_zero_duration() {
        let req = request(&[vec![span("weird", 1, 0, 500, 100)]]);
        let recs =
            OtlpTracesDecoder::new().decode_traces(&batch(vec![req.encode_to_vec()], TRACES_META));
        assert_eq!(recs[0].duration_nanos, 0);
    }

    #[test]
    fn events_and_links_are_carried() {
        let mut s = span("with-extras", 2, 2, 1, 2);
        s.events = vec![Event {
            time_unix_nano: 9,
            name: "exception".into(),
            attributes: vec![kv("exception.type", "Oops")],
            dropped_attributes_count: 0,
        }];
        s.links = vec![Link {
            trace_id: vec![0x01; 16],
            span_id: vec![0x02; 8],
            trace_state: "k=v".into(),
            attributes: vec![kv("l", "1")],
            dropped_attributes_count: 0,
            flags: 0,
        }];
        let req = request(&[vec![s]]);
        let recs =
            OtlpTracesDecoder::new().decode_traces(&batch(vec![req.encode_to_vec()], TRACES_META));
        assert_eq!(recs[0].events.len(), 1);
        assert_eq!(recs[0].events[0].name, "exception");
        assert_eq!(
            recs[0].events[0]
                .attributes
                .get("exception.type")
                .map(String::as_str),
            Some("Oops")
        );
        assert_eq!(recs[0].links[0].trace_id_hex, "01".repeat(16));
        assert_eq!(recs[0].links[0].trace_state, "k=v");
    }

    #[test]
    fn malformed_entry_is_counted_and_skipped_not_fatal() {
        let good = request(&[vec![span("ok", 2, 0, 1, 2)]]).encode_to_vec();
        let b = batch(vec![b"definitely not protobuf".to_vec(), good], TRACES_META);
        let d = OtlpTracesDecoder::new();
        let recs = d.decode_traces(&b);
        assert_eq!(recs.len(), 1, "good entry still decoded");
        assert_eq!(recs[0].source.entry_index, 1);
        assert_eq!(d.counters().malformed_entries(), 1);

        // The Decoder trait path must also succeed and account for the range.
        let out = d
            .decode(b)
            .expect("decode must not fail on malformed payload");
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].source_entry_count, 2);
    }

    #[test]
    fn empty_request_yields_one_zero_record_batch() {
        let b = batch(
            vec![
                ExportTraceServiceRequest {
                    resource_spans: vec![],
                }
                .encode_to_vec(),
            ],
            TRACES_META,
        );
        let out = OtlpTracesDecoder::new().decode(b).unwrap();
        assert_eq!(out.len(), 1, "range must stay accounted for");
        let opendata_ingest_runtime::decoded_batch::DecodedRecords::Typed(t) = &out[0].records;
        assert_eq!(t.record_count(), 0);
    }

    #[test]
    fn accepts_only_traces_envelope_and_wrong_signal_is_fatal() {
        let d = OtlpTracesDecoder::new();
        assert!(d.accepts(TRACES_META));
        assert!(!d.accepts(LOGS_META));
        let wrong = batch(
            vec![request(&[vec![span("x", 1, 0, 1, 2)]]).encode_to_vec()],
            LOGS_META,
        );
        assert!(
            d.decode(wrong).is_err(),
            "logs envelope on traces pipeline must be fatal"
        );
    }
}
