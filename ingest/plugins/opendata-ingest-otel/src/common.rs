//! Helpers shared by the traces and metrics decoders (the logs decoder predates
//! this module and keeps its own copies).

use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

use opentelemetry_proto::tonic::common::v1::{InstrumentationScope, KeyValue};
use opentelemetry_proto::tonic::resource::v1::Resource;

use opendata_ingest_runtime::decoded_batch::{
    BatchStats, DecodedBatch, DecodedRecords, SourceCoordinateColumns, TypedRecords,
};
use opendata_ingest_runtime::error::{RuntimeError, RuntimeResult};
use opendata_ingest_runtime::identity::SchemaVersion;
use opendata_ingest_runtime::source::{SourceBatch, SourceEntry};

use crate::envelope::{ConfiguredEnvelope, decode_envelopes, validate_consistent};
use crate::logs::{RowSourceCoordinates, merge_attributes};

/// Counters a decoder exposes so the binary can publish them as metrics.
#[derive(Debug, Default)]
pub struct DecodeCounters {
    malformed_entries: AtomicU64,
    skipped_points: AtomicU64,
}

impl DecodeCounters {
    pub fn malformed_entries(&self) -> u64 {
        self.malformed_entries.load(Ordering::Relaxed)
    }

    /// Data points / spans deliberately not stored (unsupported metric types).
    pub fn skipped_points(&self) -> u64 {
        self.skipped_points.load(Ordering::Relaxed)
    }

    pub(crate) fn record_malformed(&self) {
        self.malformed_entries.fetch_add(1, Ordering::Relaxed);
    }

    pub(crate) fn record_skipped(&self, n: u64) {
        self.skipped_points.fetch_add(n, Ordering::Relaxed);
    }
}

/// Envelope validation: a short, unknown or mismatched envelope is fatal (it
/// means the wrong signal was routed to this pipeline, which is a deployment
/// bug rather than bad data).
pub(crate) fn validate_envelopes(
    batch: &SourceBatch,
    expected: &ConfiguredEnvelope,
) -> RuntimeResult<()> {
    let envelopes = decode_envelopes(batch).map_err(|e| RuntimeError::Decoder(Box::new(e)))?;
    validate_consistent(&envelopes, expected).map_err(|e| RuntimeError::Decoder(Box::new(e)))
}

pub(crate) fn row_coords(
    batch: &SourceBatch,
    entry: &SourceEntry,
    record_index: u32,
) -> RowSourceCoordinates {
    RowSourceCoordinates {
        buffer_sequence: batch.sequence,
        entry_index: entry.entry_index,
        record_index,
        manifest_path: batch.manifest_path.clone(),
        data_path: batch.data_object_path.clone(),
        ingestion_time_ms: entry.ingestion_time_ms,
    }
}

pub(crate) fn attrs_to_map(attrs: &[KeyValue]) -> BTreeMap<String, String> {
    let mut m = BTreeMap::new();
    merge_attributes(attrs, &mut m);
    m
}

/// Resource-level fields every record of a `ResourceX` block inherits.
#[derive(Debug, Clone, Default)]
pub(crate) struct ResourceInfo {
    pub service_name: Option<String>,
    pub attributes: BTreeMap<String, String>,
    pub schema_url: String,
}

pub(crate) fn resource_info(resource: &Option<Resource>, schema_url: &str) -> ResourceInfo {
    let attributes = resource
        .as_ref()
        .map(|r| attrs_to_map(&r.attributes))
        .unwrap_or_default();
    ResourceInfo {
        service_name: attributes.get("service.name").cloned(),
        attributes,
        schema_url: schema_url.to_string(),
    }
}

/// Scope-level fields.
#[derive(Debug, Clone, Default)]
pub(crate) struct ScopeInfo {
    pub name: String,
    pub version: String,
    pub attributes: BTreeMap<String, String>,
    pub dropped_attributes_count: u32,
    pub schema_url: String,
}

pub(crate) fn scope_info(scope: &Option<InstrumentationScope>, schema_url: &str) -> ScopeInfo {
    match scope {
        Some(s) => ScopeInfo {
            name: s.name.clone(),
            version: s.version.clone(),
            attributes: attrs_to_map(&s.attributes),
            dropped_attributes_count: s.dropped_attributes_count,
            schema_url: schema_url.to_string(),
        },
        None => ScopeInfo {
            schema_url: schema_url.to_string(),
            ..Default::default()
        },
    }
}

/// Assemble the runtime `DecodedBatch` carrying `typed` plus the per-row
/// source-coordinate columns derived from `coords`.
pub(crate) fn assemble_batch<'a>(
    batch: &SourceBatch,
    coords: impl Iterator<Item = &'a RowSourceCoordinates>,
    decoded_byte_estimate: u64,
    typed: Arc<dyn TypedRecords>,
) -> DecodedBatch {
    let mut sequences = Vec::new();
    let mut entry_indices = Vec::new();
    let mut record_indices = Vec::new();
    let mut ingestion_time_ms = Vec::new();
    for c in coords {
        sequences.push(c.buffer_sequence);
        entry_indices.push(c.entry_index);
        record_indices.push(c.record_index);
        ingestion_time_ms.push(c.ingestion_time_ms);
    }
    DecodedBatch {
        source: batch.source.clone(),
        low_sequence: batch.sequence,
        high_sequence: batch.sequence,
        source_entry_count: batch.entries.len() as u32,
        records: DecodedRecords::Typed(typed),
        source_columns: SourceCoordinateColumns {
            manifest_path: batch.manifest_path.clone(),
            data_path: batch.data_object_path.clone(),
            sequences,
            entry_indices,
            record_indices,
            ingestion_time_ms,
        },
        stats: BatchStats {
            source_byte_count: 0,
            decoded_byte_estimate,
        },
        schema_version: SchemaVersion(1),
    }
}

pub(crate) fn map_bytes(m: &BTreeMap<String, String>) -> usize {
    m.iter().map(|(k, v)| k.len() + v.len()).sum()
}
