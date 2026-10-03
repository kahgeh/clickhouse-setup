-- 00_database_users.sql
-- Database + least-privilege users. DEV-ONLY credentials; in AWS these come from env/secrets.
CREATE DATABASE IF NOT EXISTS otel;
CREATE USER IF NOT EXISTS hyperdx_ro IDENTIFIED BY 'hyperdx_ro_dev';
GRANT SELECT ON otel.* TO hyperdx_ro;

-- 10_logs.sql
-- ClickStack-shaped logs table (column names match HyperDX's default otel_logs) + OpenData provenance.
CREATE TABLE IF NOT EXISTS otel.otel_logs
(
    Timestamp          DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    TraceId            String CODEC(ZSTD(1)),
    SpanId             String CODEC(ZSTD(1)),
    TraceFlags         UInt8,
    SeverityText       LowCardinality(String) CODEC(ZSTD(1)),
    SeverityNumber     UInt8,
    ServiceName        LowCardinality(String) CODEC(ZSTD(1)),
    Body               String CODEC(ZSTD(1)),
    ResourceSchemaUrl  LowCardinality(String) CODEC(ZSTD(1)),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeSchemaUrl     LowCardinality(String) CODEC(ZSTD(1)),
    ScopeName          String CODEC(ZSTD(1)),
    ScopeVersion       LowCardinality(String) CODEC(ZSTD(1)),
    ScopeAttributes    Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    LogAttributes      Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    EventName          String CODEC(ZSTD(1)),
    _odb_sequence          UInt64,
    _odb_entry_index       UInt32,
    _odb_record_index      UInt32,
    _odb_manifest_path     LowCardinality(String),
    _odb_data_path         String,
    _odb_ingestion_time_ms Int64,
    _adapter_version       UInt32,
    -- Server-side insert time: end-to-end latency = _odb_clickhouse_inserted_at - event time.
    _odb_clickhouse_inserted_at DateTime64(9) DEFAULT now64(9),
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_log_attr_key mapKeys(LogAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_lower_body lower(Body) TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 8
)
ENGINE = ReplacingMergeTree(_adapter_version)
PARTITION BY toDate(Timestamp)
ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp, _odb_sequence, _odb_entry_index, _odb_record_index)
TTL toDateTime(Timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1, non_replicated_deduplication_window = 1000;

-- 20_traces.sql
-- ClickStack-shaped traces table (HyperDX default otel_traces columns) + provenance.
CREATE TABLE IF NOT EXISTS otel.otel_traces
(
    Timestamp          DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    TraceId            String CODEC(ZSTD(1)),
    SpanId             String CODEC(ZSTD(1)),
    ParentSpanId       String CODEC(ZSTD(1)),
    TraceState         String CODEC(ZSTD(1)),
    SpanName           LowCardinality(String) CODEC(ZSTD(1)),
    SpanKind           LowCardinality(String) CODEC(ZSTD(1)),
    ServiceName        LowCardinality(String) CODEC(ZSTD(1)),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeName          String CODEC(ZSTD(1)),
    ScopeVersion       String CODEC(ZSTD(1)),
    SpanAttributes     Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    Duration           UInt64 CODEC(ZSTD(1)),
    StatusCode         LowCardinality(String) CODEC(ZSTD(1)),
    StatusMessage      String CODEC(ZSTD(1)),
    `Events.Timestamp`  Array(DateTime64(9)) CODEC(ZSTD(1)),
    `Events.Name`       Array(LowCardinality(String)) CODEC(ZSTD(1)),
    `Events.Attributes` Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    `Links.TraceId`     Array(String) CODEC(ZSTD(1)),
    `Links.SpanId`      Array(String) CODEC(ZSTD(1)),
    `Links.TraceState`  Array(String) CODEC(ZSTD(1)),
    `Links.Attributes`  Array(Map(LowCardinality(String), String)) CODEC(ZSTD(1)),
    _odb_sequence          UInt64,
    _odb_entry_index       UInt32,
    _odb_record_index      UInt32,
    _odb_manifest_path     LowCardinality(String),
    _odb_data_path         String,
    _odb_ingestion_time_ms Int64,
    _adapter_version       UInt32,
    -- Server-side insert time: end-to-end latency = _odb_clickhouse_inserted_at - event time.
    _odb_clickhouse_inserted_at DateTime64(9) DEFAULT now64(9),
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_span_attr_key mapKeys(SpanAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_duration Duration TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_adapter_version)
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SpanName, toDateTime(Timestamp), _odb_sequence, _odb_entry_index, _odb_record_index)
TTL toDate(Timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1, non_replicated_deduplication_window = 1000;

-- 30_metrics_gauge.sql
-- ClickStack-shaped otel_metrics_gauge (HyperDX default columns; exemplars omitted) + provenance.
CREATE TABLE IF NOT EXISTS otel.otel_metrics_gauge
(
    ResourceAttributes    Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl     String CODEC(ZSTD(1)),
    ScopeName             String CODEC(ZSTD(1)),
    ScopeVersion          String CODEC(ZSTD(1)),
    ScopeAttributes       Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl        String CODEC(ZSTD(1)),
    ServiceName           LowCardinality(String) CODEC(ZSTD(1)),
    MetricName            String CODEC(ZSTD(1)),
    MetricDescription     String CODEC(ZSTD(1)),
    MetricUnit            String CODEC(ZSTD(1)),
    Attributes            Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix         DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    TimeUnix              DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    Value Float64 CODEC(ZSTD(1)),
    Flags UInt32 CODEC(ZSTD(1)),
    _odb_sequence          UInt64,
    _odb_entry_index       UInt32,
    _odb_record_index      UInt32,
    _odb_manifest_path     LowCardinality(String),
    _odb_data_path         String,
    _odb_ingestion_time_ms Int64,
    _adapter_version       UInt32,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_adapter_version)
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix), _odb_sequence, _odb_entry_index, _odb_record_index)
TTL toDateTime(TimeUnix) + INTERVAL 30 DAY
SETTINGS ttl_only_drop_parts = 1, non_replicated_deduplication_window = 1000;

-- 31_metrics_sum.sql
-- ClickStack-shaped otel_metrics_sum (HyperDX default columns; exemplars omitted) + provenance.
CREATE TABLE IF NOT EXISTS otel.otel_metrics_sum
(
    ResourceAttributes    Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl     String CODEC(ZSTD(1)),
    ScopeName             String CODEC(ZSTD(1)),
    ScopeVersion          String CODEC(ZSTD(1)),
    ScopeAttributes       Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl        String CODEC(ZSTD(1)),
    ServiceName           LowCardinality(String) CODEC(ZSTD(1)),
    MetricName            String CODEC(ZSTD(1)),
    MetricDescription     String CODEC(ZSTD(1)),
    MetricUnit            String CODEC(ZSTD(1)),
    Attributes            Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix         DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    TimeUnix              DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    Value Float64 CODEC(ZSTD(1)),
    Flags UInt32 CODEC(ZSTD(1)),
    AggregationTemporality Int32 CODEC(ZSTD(1)),
    IsMonotonic Bool CODEC(ZSTD(1)),
    _odb_sequence          UInt64,
    _odb_entry_index       UInt32,
    _odb_record_index      UInt32,
    _odb_manifest_path     LowCardinality(String),
    _odb_data_path         String,
    _odb_ingestion_time_ms Int64,
    _adapter_version       UInt32,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_adapter_version)
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix), _odb_sequence, _odb_entry_index, _odb_record_index)
TTL toDateTime(TimeUnix) + INTERVAL 30 DAY
SETTINGS ttl_only_drop_parts = 1, non_replicated_deduplication_window = 1000;

-- 32_metrics_histogram.sql
-- ClickStack-shaped otel_metrics_histogram (HyperDX default columns; exemplars omitted) + provenance.
CREATE TABLE IF NOT EXISTS otel.otel_metrics_histogram
(
    ResourceAttributes    Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl     String CODEC(ZSTD(1)),
    ScopeName             String CODEC(ZSTD(1)),
    ScopeVersion          String CODEC(ZSTD(1)),
    ScopeAttributes       Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl        String CODEC(ZSTD(1)),
    ServiceName           LowCardinality(String) CODEC(ZSTD(1)),
    MetricName            String CODEC(ZSTD(1)),
    MetricDescription     String CODEC(ZSTD(1)),
    MetricUnit            String CODEC(ZSTD(1)),
    Attributes            Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix         DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    TimeUnix              DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    Count UInt64 CODEC(Delta(8), ZSTD(1)),
    Sum Float64 CODEC(ZSTD(1)),
    BucketCounts Array(UInt64) CODEC(ZSTD(1)),
    ExplicitBounds Array(Float64) CODEC(ZSTD(1)),
    Flags UInt32 CODEC(ZSTD(1)),
    Min Float64 CODEC(ZSTD(1)),
    Max Float64 CODEC(ZSTD(1)),
    AggregationTemporality Int32 CODEC(ZSTD(1)),
    _odb_sequence          UInt64,
    _odb_entry_index       UInt32,
    _odb_record_index      UInt32,
    _odb_manifest_path     LowCardinality(String),
    _odb_data_path         String,
    _odb_ingestion_time_ms Int64,
    _adapter_version       UInt32,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_adapter_version)
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix), _odb_sequence, _odb_entry_index, _odb_record_index)
TTL toDateTime(TimeUnix) + INTERVAL 30 DAY
SETTINGS ttl_only_drop_parts = 1, non_replicated_deduplication_window = 1000;

