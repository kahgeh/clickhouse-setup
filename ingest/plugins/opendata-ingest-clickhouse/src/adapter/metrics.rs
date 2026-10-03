//! OTLP metrics ClickHouse adapter: fans data points out to the ClickStack-shaped
//! `otel_metrics_gauge`, `otel_metrics_sum` and `otel_metrics_histogram` tables
//! (see `stack/clickhouse/3*_metrics_*.sql`). Exemplars are not stored.

use serde::{Deserialize, Serialize};

use opendata_ingest_otel::logs::RowSourceCoordinates;
use opendata_ingest_otel::metrics::{DecodedMetricPoint, MetricData, MetricPointHeader};

use super::common::{
    AdapterKind, ChunkRecord, CommonAdapterConfig, PROVENANCE_COLUMNS, Route, plan_chunks,
    provenance_values,
};
use super::{Adapter, AdapterResult, ClickHouseAdapterBatch, InsertChunk, RowValue};

const KIND: AdapterKind = AdapterKind {
    tag: "otlp-metrics-clickhouse-adapter:v1",
    signal: "metrics",
};

impl ChunkRecord for DecodedMetricPoint {
    fn coords(&self) -> &RowSourceCoordinates {
        &self.header.source
    }
    fn approx_size_bytes(&self) -> usize {
        DecodedMetricPoint::approx_size_bytes(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MetricsAdapterConfig {
    #[serde(flatten)]
    pub common: CommonAdapterConfig,
    pub gauge_table: String,
    pub sum_table: String,
    pub histogram_table: String,
}

impl Default for MetricsAdapterConfig {
    fn default() -> Self {
        Self {
            common: CommonAdapterConfig::default(),
            gauge_table: "otel_metrics_gauge".to_string(),
            sum_table: "otel_metrics_sum".to_string(),
            histogram_table: "otel_metrics_histogram".to_string(),
        }
    }
}

macro_rules! columns {
    ($($data:literal),* $(,)?) => {
        &[
            "ResourceAttributes", "ResourceSchemaUrl", "ScopeName", "ScopeVersion",
            "ScopeAttributes", "ScopeDroppedAttrCount", "ScopeSchemaUrl", "ServiceName",
            "MetricName", "MetricDescription", "MetricUnit", "Attributes",
            "StartTimeUnix", "TimeUnix",
            $($data,)*
            PROVENANCE_COLUMNS[0], PROVENANCE_COLUMNS[1], PROVENANCE_COLUMNS[2],
            PROVENANCE_COLUMNS[3], PROVENANCE_COLUMNS[4], PROVENANCE_COLUMNS[5],
            PROVENANCE_COLUMNS[6],
        ]
    };
}

pub const GAUGE_COLUMNS: &[&str] = columns!["Value", "Flags"];
pub const SUM_COLUMNS: &[&str] =
    columns!["Value", "Flags", "AggregationTemporality", "IsMonotonic"];
pub const HISTOGRAM_COLUMNS: &[&str] = columns![
    "Count",
    "Sum",
    "BucketCounts",
    "ExplicitBounds",
    "Flags",
    "Min",
    "Max",
    "AggregationTemporality",
];

#[derive(Debug, Clone)]
pub struct OtlpMetricsClickHouseAdapter {
    config: MetricsAdapterConfig,
}

impl OtlpMetricsClickHouseAdapter {
    pub fn new(config: MetricsAdapterConfig) -> Self {
        Self { config }
    }

    pub fn config(&self) -> &MetricsAdapterConfig {
        &self.config
    }
}

impl Adapter for OtlpMetricsClickHouseAdapter {
    type Input = DecodedMetricPoint;

    fn plan(&self, batch: ClickHouseAdapterBatch<Self::Input>) -> AdapterResult<Vec<InsertChunk>> {
        let ClickHouseAdapterBatch {
            identity, records, ..
        } = batch;
        let version = self.config.common.adapter_version;
        let cfg = &self.config;
        plan_chunks(
            &identity,
            records,
            &cfg.common,
            KIND,
            |r| match r.data {
                MetricData::Gauge { .. } => Route {
                    table: &cfg.gauge_table,
                    columns: GAUGE_COLUMNS,
                },
                MetricData::Sum { .. } => Route {
                    table: &cfg.sum_table,
                    columns: SUM_COLUMNS,
                },
                MetricData::Histogram { .. } => Route {
                    table: &cfg.histogram_table,
                    columns: HISTOGRAM_COLUMNS,
                },
            },
            |r| point_row(r, version),
        )
    }
}

fn header_values(h: &MetricPointHeader) -> Vec<RowValue> {
    vec![
        RowValue::StringMap(h.resource_attributes.clone()),
        RowValue::String(h.resource_schema_url.clone()),
        RowValue::String(h.scope_name.clone()),
        RowValue::String(h.scope_version.clone()),
        RowValue::StringMap(h.scope_attributes.clone()),
        RowValue::UInt32(h.scope_dropped_attr_count),
        RowValue::String(h.scope_schema_url.clone()),
        RowValue::LowCardinalityString(h.service_name.clone().unwrap_or_default()),
        RowValue::String(h.metric_name.clone()),
        RowValue::String(h.metric_description.clone()),
        RowValue::String(h.metric_unit.clone()),
        RowValue::StringMap(h.attributes.clone()),
        RowValue::DateTime64Nanos(h.start_time_unix_nano),
        RowValue::DateTime64Nanos(h.time_unix_nano),
    ]
}

fn point_row(p: &DecodedMetricPoint, adapter_version: u32) -> Vec<RowValue> {
    let h = &p.header;
    let mut row = header_values(h);
    match &p.data {
        MetricData::Gauge { value } => {
            row.push(RowValue::Float64(*value));
            row.push(RowValue::UInt32(h.flags));
        }
        MetricData::Sum {
            value,
            aggregation_temporality,
            is_monotonic,
        } => {
            row.push(RowValue::Float64(*value));
            row.push(RowValue::UInt32(h.flags));
            row.push(RowValue::Int32(*aggregation_temporality));
            row.push(RowValue::Bool(*is_monotonic));
        }
        MetricData::Histogram {
            count,
            sum,
            bucket_counts,
            explicit_bounds,
            min,
            max,
            aggregation_temporality,
        } => {
            row.push(RowValue::UInt64(*count));
            row.push(RowValue::Float64(*sum));
            row.push(RowValue::ArrayUInt64(bucket_counts.clone()));
            row.push(RowValue::ArrayFloat64(explicit_bounds.clone()));
            row.push(RowValue::UInt32(h.flags));
            row.push(RowValue::Float64(*min));
            row.push(RowValue::Float64(*max));
            row.push(RowValue::Int32(*aggregation_temporality));
        }
    }
    row.extend(provenance_values(&h.source, adapter_version));
    row
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use super::*;
    use opendata_ingest_otel::logs::RowSourceCoordinates;
    use opendata_ingest_runtime::identity::{CommitIdentity, SchemaVersion, SequenceRange};
    use opendata_ingest_runtime::sink::SinkId;
    use opendata_ingest_runtime::source::SourceId;

    fn header(idx: u32) -> MetricPointHeader {
        MetricPointHeader {
            source: RowSourceCoordinates {
                buffer_sequence: 4,
                entry_index: 0,
                record_index: idx,
                manifest_path: "otel/metrics/manifest".into(),
                data_path: "otel/metrics/data/x".into(),
                ingestion_time_ms: 3,
            },
            service_name: Some("web".into()),
            resource_attributes: BTreeMap::from([("service.name".into(), "web".into())]),
            resource_schema_url: String::new(),
            scope_name: "web".into(),
            scope_version: "0.1".into(),
            scope_attributes: BTreeMap::new(),
            scope_dropped_attr_count: 0,
            scope_schema_url: String::new(),
            metric_name: "m".into(),
            metric_description: "d".into(),
            metric_unit: "s".into(),
            attributes: BTreeMap::from([("route".into(), "/ok".into())]),
            start_time_unix_nano: 1_700_000_000_000_000_000,
            time_unix_nano: 1_700_000_001_000_000_000,
            flags: 0,
        }
    }

    fn gauge(idx: u32) -> DecodedMetricPoint {
        DecodedMetricPoint {
            header: header(idx),
            data: MetricData::Gauge { value: 1.5 },
        }
    }
    fn sum(idx: u32) -> DecodedMetricPoint {
        DecodedMetricPoint {
            header: header(idx),
            data: MetricData::Sum {
                value: 10.0,
                aggregation_temporality: 2,
                is_monotonic: true,
            },
        }
    }
    fn hist(idx: u32) -> DecodedMetricPoint {
        DecodedMetricPoint {
            header: header(idx),
            data: MetricData::Histogram {
                count: 6,
                sum: 1.5,
                bucket_counts: vec![1, 2, 3],
                explicit_bounds: vec![0.1, 0.5],
                min: 0.01,
                max: 0.9,
                aggregation_temporality: 2,
            },
        }
    }

    fn plan(records: Vec<DecodedMetricPoint>) -> Vec<InsertChunk> {
        OtlpMetricsClickHouseAdapter::new(MetricsAdapterConfig::default())
            .plan(ClickHouseAdapterBatch {
                identity: CommitIdentity {
                    source: SourceId::from("buffer"),
                    sink: SinkId::from("clickhouse_metrics"),
                    range: SequenceRange::new(4, 4),
                    schema_version: SchemaVersion(1),
                },
                records,
                bytes: 0,
            })
            .expect("plan")
    }

    #[test]
    fn fans_out_by_metric_type_to_three_tables_and_conserves_rows() {
        let chunks = plan(vec![gauge(0), sum(1), hist(2), gauge(3), hist(4)]);
        let by_table: BTreeMap<_, _> = chunks
            .iter()
            .map(|c| (c.table.as_str(), c.rows.len()))
            .collect();
        assert_eq!(
            by_table,
            BTreeMap::from([
                ("otel_metrics_gauge", 2),
                ("otel_metrics_histogram", 2),
                ("otel_metrics_sum", 1),
            ])
        );
        assert_eq!(chunks.iter().map(|c| c.rows.len()).sum::<usize>(), 5);
    }

    #[test]
    fn rows_align_with_each_tables_columns() {
        for chunk in plan(vec![gauge(0), sum(1), hist(2)]) {
            for row in &chunk.rows {
                assert_eq!(row.len(), chunk.columns.len(), "table {}", chunk.table);
            }
        }
    }

    #[test]
    fn histogram_json_matches_clickstack_columns() {
        let chunks = plan(vec![hist(0)]);
        let c = &chunks[0];
        let json: serde_json::Map<String, serde_json::Value> = c
            .columns
            .iter()
            .zip(&c.rows[0])
            .map(|(k, v)| (k.to_string(), v.to_json()))
            .collect();
        assert_eq!(json["Count"], 6);
        assert_eq!(json["Sum"], 1.5);
        assert_eq!(json["BucketCounts"], serde_json::json!([1, 2, 3]));
        assert_eq!(json["ExplicitBounds"], serde_json::json!([0.1, 0.5]));
        assert_eq!(json["MetricName"], "m");
        assert_eq!(json["Attributes"], serde_json::json!({"route": "/ok"}));
        assert_eq!(json["ServiceName"], "web");
        assert_eq!(json["AggregationTemporality"], 2);
    }

    #[test]
    fn sum_json_has_monotonic_flag_and_temporality() {
        let chunks = plan(vec![sum(0)]);
        let c = &chunks[0];
        let i = c.columns.iter().position(|k| *k == "IsMonotonic").unwrap();
        assert_eq!(c.rows[0][i].to_json(), serde_json::json!(true));
        let t = c
            .columns
            .iter()
            .position(|k| *k == "AggregationTemporality")
            .unwrap();
        assert_eq!(c.rows[0][t].to_json(), serde_json::json!(2));
    }

    #[test]
    fn tokens_are_per_table_and_stable_under_reordering() {
        let a = plan(vec![gauge(0), sum(1), hist(2)]);
        let b = plan(vec![hist(2), gauge(0), sum(1)]);
        let ta: Vec<_> = a.iter().map(|c| c.idempotency_token.clone()).collect();
        let tb: Vec<_> = b.iter().map(|c| c.idempotency_token.clone()).collect();
        assert_eq!(ta, tb);
        let unique: std::collections::BTreeSet<_> = ta.iter().collect();
        assert_eq!(unique.len(), 3);
    }

    #[test]
    fn non_finite_floats_do_not_break_json() {
        let mut p = hist(0);
        if let MetricData::Histogram { sum, .. } = &mut p.data {
            *sum = f64::NAN;
        }
        let chunks = plan(vec![p]);
        let c = &chunks[0];
        let i = c.columns.iter().position(|k| *k == "Sum").unwrap();
        assert_eq!(c.rows[0][i].to_json(), serde_json::Value::Null);
    }
}
