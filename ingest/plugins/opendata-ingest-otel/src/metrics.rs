//! OTLP metrics decoder.
//!
//! Flattens `ResourceMetrics -> ScopeMetrics -> Metric -> DataPoint` into one
//! [`DecodedMetricPoint`] per data point. Supported metric types: gauge, sum,
//! histogram (the three HyperDX can chart). Summaries and exponential
//! histograms are counted in [`DecodeCounters::skipped_points`] and not stored.
//!
//! As with traces, a payload that is not valid OTLP protobuf is counted and
//! skipped rather than failing the pipeline; a wrong-signal envelope is fatal.

use std::any::Any;
use std::collections::BTreeMap;
use std::sync::Arc;

use opentelemetry_proto::tonic::collector::metrics::v1::ExportMetricsServiceRequest;
use opentelemetry_proto::tonic::metrics::v1::{
    HistogramDataPoint, NumberDataPoint, metric::Data, number_data_point::Value as NumberValue,
};
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

pub const OTLP_METRICS_SCHEMA_NAME: &str = "opendata.otel.metrics.v1";

/// Identity and timing shared by every data point.
#[derive(Debug, Clone)]
pub struct MetricPointHeader {
    pub source: RowSourceCoordinates,
    pub service_name: Option<String>,
    pub resource_attributes: BTreeMap<String, String>,
    pub resource_schema_url: String,
    pub scope_name: String,
    pub scope_version: String,
    pub scope_attributes: BTreeMap<String, String>,
    pub scope_dropped_attr_count: u32,
    pub scope_schema_url: String,
    pub metric_name: String,
    pub metric_description: String,
    pub metric_unit: String,
    pub attributes: BTreeMap<String, String>,
    pub start_time_unix_nano: u64,
    pub time_unix_nano: u64,
    pub flags: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub enum MetricData {
    Gauge {
        value: f64,
    },
    Sum {
        value: f64,
        aggregation_temporality: i32,
        is_monotonic: bool,
    },
    Histogram {
        count: u64,
        sum: f64,
        bucket_counts: Vec<u64>,
        explicit_bounds: Vec<f64>,
        min: f64,
        max: f64,
        aggregation_temporality: i32,
    },
}

#[derive(Debug, Clone)]
pub struct DecodedMetricPoint {
    pub header: MetricPointHeader,
    pub data: MetricData,
}

impl DecodedMetricPoint {
    pub fn approx_size_bytes(&self) -> usize {
        let h = &self.header;
        let mut sz = std::mem::size_of::<Self>();
        sz += h.service_name.as_ref().map_or(0, |s| s.len());
        sz += map_bytes(&h.resource_attributes) + map_bytes(&h.scope_attributes);
        sz += map_bytes(&h.attributes);
        sz += h.metric_name.len() + h.metric_description.len() + h.metric_unit.len();
        sz += h.scope_name.len() + h.scope_version.len();
        if let MetricData::Histogram {
            bucket_counts,
            explicit_bounds,
            ..
        } = &self.data
        {
            sz += 8 * (bucket_counts.len() + explicit_bounds.len());
        }
        sz
    }
}

#[derive(Debug)]
pub struct TypedDecodedMetrics {
    records: Vec<DecodedMetricPoint>,
    schema: TypedSchema,
}

impl TypedDecodedMetrics {
    pub fn new(records: Vec<DecodedMetricPoint>) -> Self {
        Self {
            records,
            schema: TypedSchema {
                name: OTLP_METRICS_SCHEMA_NAME.into(),
                version: SchemaVersion(1),
            },
        }
    }

    pub fn records(&self) -> &[DecodedMetricPoint] {
        &self.records
    }
}

impl TypedRecords for TypedDecodedMetrics {
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
pub struct OtlpMetricsDecoder {
    expected: ConfiguredEnvelope,
    counters: Arc<DecodeCounters>,
}

impl Default for OtlpMetricsDecoder {
    fn default() -> Self {
        Self::new()
    }
}

fn number_value(dp: &NumberDataPoint) -> f64 {
    match dp.value {
        Some(NumberValue::AsDouble(d)) => d,
        Some(NumberValue::AsInt(i)) => i as f64,
        None => 0.0,
    }
}

impl OtlpMetricsDecoder {
    pub fn new() -> Self {
        Self {
            expected: ConfiguredEnvelope {
                version: 1,
                signal_type: SignalType::Metrics,
                encoding: PayloadEncoding::OtlpProtobuf,
            },
            counters: Arc::new(DecodeCounters::default()),
        }
    }

    pub fn counters(&self) -> Arc<DecodeCounters> {
        Arc::clone(&self.counters)
    }

    pub fn decode_metrics(&self, batch: &SourceBatch) -> Vec<DecodedMetricPoint> {
        let mut out = Vec::new();
        for entry in &batch.entries {
            let req = match ExportMetricsServiceRequest::decode(entry.raw_bytes.as_ref()) {
                Ok(req) => req,
                Err(e) => {
                    self.counters.record_malformed();
                    tracing::warn!(
                        sequence = batch.sequence,
                        entry_index = entry.entry_index,
                        error = %e,
                        "skipping malformed OTLP metrics entry"
                    );
                    continue;
                }
            };
            let mut record_index: u32 = 0;
            for rm in &req.resource_metrics {
                let res = resource_info(&rm.resource, &rm.schema_url);
                for sm in &rm.scope_metrics {
                    let scope = scope_info(&sm.scope, &sm.schema_url);
                    for metric in &sm.metrics {
                        let mut header_for =
                            |attributes: &[_], start: u64, time: u64, flags: u32| {
                                let h = MetricPointHeader {
                                    source: row_coords(batch, entry, record_index),
                                    service_name: res.service_name.clone(),
                                    resource_attributes: res.attributes.clone(),
                                    resource_schema_url: res.schema_url.clone(),
                                    scope_name: scope.name.clone(),
                                    scope_version: scope.version.clone(),
                                    scope_attributes: scope.attributes.clone(),
                                    scope_dropped_attr_count: scope.dropped_attributes_count,
                                    scope_schema_url: scope.schema_url.clone(),
                                    metric_name: metric.name.clone(),
                                    metric_description: metric.description.clone(),
                                    metric_unit: metric.unit.clone(),
                                    attributes: attrs_to_map(attributes),
                                    start_time_unix_nano: start,
                                    time_unix_nano: time,
                                    flags,
                                };
                                record_index += 1;
                                h
                            };
                        match &metric.data {
                            Some(Data::Gauge(g)) => {
                                for dp in &g.data_points {
                                    out.push(DecodedMetricPoint {
                                        header: header_for(
                                            &dp.attributes,
                                            dp.start_time_unix_nano,
                                            dp.time_unix_nano,
                                            dp.flags,
                                        ),
                                        data: MetricData::Gauge {
                                            value: number_value(dp),
                                        },
                                    });
                                }
                            }
                            Some(Data::Sum(s)) => {
                                for dp in &s.data_points {
                                    out.push(DecodedMetricPoint {
                                        header: header_for(
                                            &dp.attributes,
                                            dp.start_time_unix_nano,
                                            dp.time_unix_nano,
                                            dp.flags,
                                        ),
                                        data: MetricData::Sum {
                                            value: number_value(dp),
                                            aggregation_temporality: s.aggregation_temporality,
                                            is_monotonic: s.is_monotonic,
                                        },
                                    });
                                }
                            }
                            Some(Data::Histogram(h)) => {
                                for dp in &h.data_points {
                                    out.push(DecodedMetricPoint {
                                        header: header_for(
                                            &dp.attributes,
                                            dp.start_time_unix_nano,
                                            dp.time_unix_nano,
                                            dp.flags,
                                        ),
                                        data: histogram_data(dp, h.aggregation_temporality),
                                    });
                                }
                            }
                            Some(Data::ExponentialHistogram(e)) => {
                                self.counters.record_skipped(e.data_points.len() as u64);
                            }
                            Some(Data::Summary(s)) => {
                                self.counters.record_skipped(s.data_points.len() as u64);
                            }
                            None => {}
                        }
                    }
                }
            }
        }
        out
    }
}

fn histogram_data(dp: &HistogramDataPoint, aggregation_temporality: i32) -> MetricData {
    MetricData::Histogram {
        count: dp.count,
        sum: dp.sum.unwrap_or(0.0),
        bucket_counts: dp.bucket_counts.clone(),
        explicit_bounds: dp.explicit_bounds.clone(),
        min: dp.min.unwrap_or(0.0),
        max: dp.max.unwrap_or(0.0),
        aggregation_temporality,
    }
}

impl Decoder for OtlpMetricsDecoder {
    fn accepts(&self, raw_metadata: &[u8]) -> bool {
        crate::envelope::decode_one(0, raw_metadata)
            .map(|env| self.expected.matches(&env))
            .unwrap_or(false)
    }

    fn decode(&self, batch: SourceBatch) -> RuntimeResult<Vec<DecodedBatch>> {
        validate_envelopes(&batch, &self.expected)?;
        let records = self.decode_metrics(&batch);
        let estimate: u64 = records.iter().map(|r| r.approx_size_bytes() as u64).sum();
        let coords: Vec<RowSourceCoordinates> =
            records.iter().map(|r| r.header.source.clone()).collect();
        let typed = Arc::new(TypedDecodedMetrics::new(records));
        Ok(vec![assemble_batch(&batch, coords.iter(), estimate, typed)])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bytes::Bytes;
    use opendata_ingest_runtime::source::SourceEntry;
    use opentelemetry_proto::tonic::common::v1::{AnyValue, KeyValue, any_value::Value};
    use opentelemetry_proto::tonic::metrics::v1::{
        ExponentialHistogram, ExponentialHistogramDataPoint, Gauge, Histogram, Metric,
        ResourceMetrics, ScopeMetrics, Sum, Summary, SummaryDataPoint,
    };
    use opentelemetry_proto::tonic::resource::v1::Resource;

    const METRICS_META: &[u8] = &[1, 1, 1, 0];
    const TRACES_META: &[u8] = &[1, 3, 1, 0];

    fn kv(k: &str, v: &str) -> KeyValue {
        KeyValue {
            key: k.into(),
            value: Some(AnyValue {
                value: Some(Value::StringValue(v.into())),
            }),
        }
    }

    fn num(v: NumberValue, attr: &str) -> NumberDataPoint {
        NumberDataPoint {
            attributes: vec![kv("route", attr)],
            start_time_unix_nano: 100,
            time_unix_nano: 200,
            value: Some(v),
            flags: 0,
            ..Default::default()
        }
    }

    fn metric(name: &str, data: Data) -> Metric {
        Metric {
            name: name.into(),
            description: format!("{name} desc"),
            unit: "1".into(),
            data: Some(data),
            ..Default::default()
        }
    }

    fn request(metrics: Vec<Metric>) -> ExportMetricsServiceRequest {
        ExportMetricsServiceRequest {
            resource_metrics: vec![ResourceMetrics {
                resource: Some(Resource {
                    attributes: vec![kv("service.name", "web")],
                    dropped_attributes_count: 0,
                }),
                scope_metrics: vec![ScopeMetrics {
                    scope: None,
                    metrics,
                    schema_url: String::new(),
                }],
                schema_url: String::new(),
            }],
        }
    }

    fn batch(payloads: Vec<Vec<u8>>, metadata: &'static [u8]) -> SourceBatch {
        SourceBatch {
            source: "buffer".into(),
            sequence: 3,
            manifest_path: "otel/metrics/manifest".into(),
            data_object_path: "otel/metrics/data/x".into(),
            entries: payloads
                .into_iter()
                .enumerate()
                .map(|(i, p)| SourceEntry {
                    entry_index: i as u32,
                    raw_bytes: Bytes::from(p),
                    raw_metadata: Bytes::from_static(metadata),
                    ingestion_time_ms: 9,
                })
                .collect(),
        }
    }

    fn hist_dp() -> HistogramDataPoint {
        HistogramDataPoint {
            attributes: vec![kv("route", "/chain")],
            start_time_unix_nano: 100,
            time_unix_nano: 200,
            count: 6,
            sum: Some(1.5),
            bucket_counts: vec![1, 2, 3],
            explicit_bounds: vec![0.1, 0.5],
            min: Some(0.01),
            max: Some(0.9),
            ..Default::default()
        }
    }

    #[test]
    fn gauge_double_and_int_points() {
        let req = request(vec![metric(
            "g",
            Data::Gauge(Gauge {
                data_points: vec![
                    num(NumberValue::AsDouble(2.5), "/a"),
                    num(NumberValue::AsInt(7), "/b"),
                ],
            }),
        )]);
        let recs = OtlpMetricsDecoder::new()
            .decode_metrics(&batch(vec![req.encode_to_vec()], METRICS_META));
        assert_eq!(recs.len(), 2);
        assert_eq!(recs[0].data, MetricData::Gauge { value: 2.5 });
        assert_eq!(recs[1].data, MetricData::Gauge { value: 7.0 });
        assert_eq!(recs[0].header.metric_name, "g");
        assert_eq!(recs[0].header.metric_description, "g desc");
        assert_eq!(recs[0].header.service_name.as_deref(), Some("web"));
        assert_eq!(
            recs[0].header.attributes.get("route").map(String::as_str),
            Some("/a")
        );
        assert_eq!(recs[0].header.start_time_unix_nano, 100);
        assert_eq!(recs[0].header.time_unix_nano, 200);
        assert_eq!(recs[1].header.source.record_index, 1);
        assert_eq!(recs[0].header.source.buffer_sequence, 3);
    }

    #[test]
    fn sum_keeps_temporality_and_monotonicity() {
        let req = request(vec![metric(
            "c",
            Data::Sum(Sum {
                data_points: vec![num(NumberValue::AsInt(41), "/x")],
                aggregation_temporality: 2,
                is_monotonic: true,
            }),
        )]);
        let recs = OtlpMetricsDecoder::new()
            .decode_metrics(&batch(vec![req.encode_to_vec()], METRICS_META));
        assert_eq!(
            recs[0].data,
            MetricData::Sum {
                value: 41.0,
                aggregation_temporality: 2,
                is_monotonic: true
            }
        );
    }

    #[test]
    fn histogram_carries_buckets_bounds_min_max() {
        let req = request(vec![metric(
            "h",
            Data::Histogram(Histogram {
                data_points: vec![hist_dp()],
                aggregation_temporality: 2,
            }),
        )]);
        let recs = OtlpMetricsDecoder::new()
            .decode_metrics(&batch(vec![req.encode_to_vec()], METRICS_META));
        assert_eq!(
            recs[0].data,
            MetricData::Histogram {
                count: 6,
                sum: 1.5,
                bucket_counts: vec![1, 2, 3],
                explicit_bounds: vec![0.1, 0.5],
                min: 0.01,
                max: 0.9,
                aggregation_temporality: 2
            }
        );
    }

    #[test]
    fn histogram_without_optional_fields_defaults_to_zero() {
        let mut dp = hist_dp();
        dp.sum = None;
        dp.min = None;
        dp.max = None;
        let req = request(vec![metric(
            "h",
            Data::Histogram(Histogram {
                data_points: vec![dp],
                aggregation_temporality: 1,
            }),
        )]);
        let recs = OtlpMetricsDecoder::new()
            .decode_metrics(&batch(vec![req.encode_to_vec()], METRICS_META));
        match &recs[0].data {
            MetricData::Histogram { sum, min, max, .. } => {
                assert_eq!((*sum, *min, *max), (0.0, 0.0, 0.0));
            }
            other => panic!("expected histogram, got {other:?}"),
        }
    }

    #[test]
    fn record_index_is_sequential_across_metric_types() {
        let req = request(vec![
            metric(
                "g",
                Data::Gauge(Gauge {
                    data_points: vec![num(NumberValue::AsInt(1), "/")],
                }),
            ),
            metric(
                "h",
                Data::Histogram(Histogram {
                    data_points: vec![hist_dp(), hist_dp()],
                    aggregation_temporality: 2,
                }),
            ),
            metric(
                "s",
                Data::Sum(Sum {
                    data_points: vec![num(NumberValue::AsInt(1), "/")],
                    aggregation_temporality: 2,
                    is_monotonic: false,
                }),
            ),
        ]);
        let recs = OtlpMetricsDecoder::new()
            .decode_metrics(&batch(vec![req.encode_to_vec()], METRICS_META));
        assert_eq!(
            recs.iter()
                .map(|r| r.header.source.record_index)
                .collect::<Vec<_>>(),
            vec![0, 1, 2, 3]
        );
    }

    #[test]
    fn summary_and_exponential_histogram_are_counted_not_stored() {
        let req = request(vec![
            metric(
                "sum",
                Data::Summary(Summary {
                    data_points: vec![SummaryDataPoint::default(), SummaryDataPoint::default()],
                }),
            ),
            metric(
                "exp",
                Data::ExponentialHistogram(ExponentialHistogram {
                    data_points: vec![ExponentialHistogramDataPoint::default()],
                    aggregation_temporality: 2,
                }),
            ),
            metric(
                "g",
                Data::Gauge(Gauge {
                    data_points: vec![num(NumberValue::AsInt(1), "/")],
                }),
            ),
        ]);
        let d = OtlpMetricsDecoder::new();
        let recs = d.decode_metrics(&batch(vec![req.encode_to_vec()], METRICS_META));
        assert_eq!(recs.len(), 1);
        assert_eq!(d.counters().skipped_points(), 3);
    }

    #[test]
    fn malformed_entry_is_counted_and_skipped_not_fatal() {
        let good = request(vec![metric(
            "g",
            Data::Gauge(Gauge {
                data_points: vec![num(NumberValue::AsInt(1), "/")],
            }),
        )])
        .encode_to_vec();
        let b = batch(vec![b"garbage-bytes!!".to_vec(), good], METRICS_META);
        let d = OtlpMetricsDecoder::new();
        let out = d.decode(b).expect("must not be fatal");
        assert_eq!(out[0].source_entry_count, 2);
        assert_eq!(d.counters().malformed_entries(), 1);
    }

    #[test]
    fn wrong_signal_envelope_is_fatal_and_accepts_is_signal_specific() {
        let d = OtlpMetricsDecoder::new();
        assert!(d.accepts(METRICS_META));
        assert!(!d.accepts(TRACES_META));
        let wrong = batch(vec![request(vec![]).encode_to_vec()], TRACES_META);
        assert!(d.decode(wrong).is_err());
    }
}
