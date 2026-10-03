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
