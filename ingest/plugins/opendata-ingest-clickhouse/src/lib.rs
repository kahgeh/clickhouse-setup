//! ClickHouse sink plugin for the generic ingest runtime.
//!
//! Provides the `adapter` and `writer` modules plus the
//! `opendata_ingest_runtime::Sink` impl that wraps `Adapter::plan(...)`
//! and `ClickHouseWriter::execute_all(...)` into the runtime's
//! `SinkCommit` / `SinkCommitFailure` contract.

pub mod adapter;
pub mod metrics;
pub mod serializer;
pub mod sink;
pub mod writer;

pub use adapter::common::CommonAdapterConfig;
pub use adapter::logs::{LogsAdapterConfig, OtlpLogsClickHouseAdapter, logs_table_ddl};
pub use adapter::metrics::{MetricsAdapterConfig, OtlpMetricsClickHouseAdapter};
pub use adapter::traces::{OtlpTracesClickHouseAdapter, TracesAdapterConfig};
pub use adapter::{
    Adapter, AdapterError, AdapterResult, ClickHouseAdapterBatch, ClickHouseSettings, InsertChunk,
    RowValue,
};
pub use serializer::{
    ChunkSerializer, JsonEachRowSerializer, RowBinarySerializer, SerializationFormat,
    build_serializer,
};
pub use sink::{ClickHouseSink, SinkRecord};
pub use writer::{ClickHouseWriter, HttpClientMode, WriterConfig, WriterError, WriterErrorClass};
