//! Deterministic chunk planning shared by the traces and metrics adapters.
//!
//! The stock logs adapter inlines the same algorithm; this module factors it
//! so that every additional signal gets identical guarantees without
//! copy-paste:
//!
//! 1. Records are sorted by `(buffer_sequence, entry_index, record_index)`.
//! 2. Records are grouped by destination table (one table for traces, up to
//!    three for metrics), preserving that order inside each group.
//! 3. Each group is split into chunks bounded by `max_chunk_rows` and
//!    `max_chunk_bytes`.
//! 4. Every chunk carries an idempotency token
//!    `{manifest}:{db}.{table}:{low}-{high}:{adapter_version}:{fingerprint}:{chunk_index}`
//!    that is a pure function of the commit identity and configuration, so a
//!    replay of the same Buffer range produces byte-identical chunks.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use opendata_ingest_otel::logs::RowSourceCoordinates;
use opendata_ingest_runtime::identity::CommitIdentity;

use super::{AdapterError, AdapterResult, ClickHouseSettings, InsertChunk, RowValue};

/// Knobs common to every adapter. Adapter-specific configs embed this.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CommonAdapterConfig {
    pub database: String,
    pub adapter_version: u32,
    /// Maximum rows per ClickHouse insert chunk.
    pub max_chunk_rows: usize,
    /// Maximum approximate bytes per ClickHouse insert chunk.
    pub max_chunk_bytes: usize,
    pub insert_quorum: Option<String>,
    /// Apply `insert_deduplication_token` on inserts. Needs a replicated table
    /// or a table created with `non_replicated_deduplication_window`.
    pub apply_deduplication_token: bool,
}

impl Default for CommonAdapterConfig {
    fn default() -> Self {
        Self {
            database: "otel".to_string(),
            adapter_version: 1,
            max_chunk_rows: 100_000,
            max_chunk_bytes: 32 * 1024 * 1024,
            insert_quorum: None,
            apply_deduplication_token: true,
        }
    }
}

/// Where one record goes: its table and the INSERT column list for that table.
#[derive(Debug, Clone, Copy)]
pub struct Route<'a> {
    pub table: &'a str,
    pub columns: &'static [&'static str],
}

/// Stable hash of every input that affects chunk boundaries or token shape.
pub fn chunking_fingerprint(adapter_tag: &str, cfg: &CommonAdapterConfig, table: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(format!("{adapter_tag}\n").as_bytes());
    hasher.update(format!("database={}\n", cfg.database).as_bytes());
    hasher.update(format!("table={table}\n").as_bytes());
    hasher.update(format!("adapter_version={}\n", cfg.adapter_version).as_bytes());
    hasher.update(format!("max_chunk_rows={}\n", cfg.max_chunk_rows).as_bytes());
    hasher.update(format!("max_chunk_bytes={}\n", cfg.max_chunk_bytes).as_bytes());
    hasher.update(
        format!(
            "insert_quorum={}\n",
            cfg.insert_quorum.as_deref().unwrap_or("")
        )
        .as_bytes(),
    );
    hex::encode(&hasher.finalize()[..8])
}

/// Build the RFC 0002 idempotency token for one chunk.
pub fn build_token(
    manifest_path: &str,
    cfg: &CommonAdapterConfig,
    table: &str,
    fingerprint: &str,
    low: u64,
    high: u64,
    chunk_index: u32,
) -> String {
    format!(
        "{manifest_path}:{}.{table}:{low}-{high}:{}:{fingerprint}:{chunk_index}",
        cfg.database, cfg.adapter_version,
    )
}

/// Provenance columns every OpenData-ingested row carries (last in each table).
pub const PROVENANCE_COLUMNS: [&str; 7] = [
    "_odb_sequence",
    "_odb_entry_index",
    "_odb_record_index",
    "_odb_manifest_path",
    "_odb_data_path",
    "_odb_ingestion_time_ms",
    "_adapter_version",
];

/// Values for [`PROVENANCE_COLUMNS`], in order.
pub fn provenance_values(src: &RowSourceCoordinates, adapter_version: u32) -> [RowValue; 7] {
    [
        RowValue::UInt64(src.buffer_sequence),
        RowValue::UInt32(src.entry_index),
        RowValue::UInt32(src.record_index),
        RowValue::LowCardinalityString(src.manifest_path.clone()),
        RowValue::String(src.data_path.clone()),
        RowValue::Int64(src.ingestion_time_ms),
        RowValue::UInt32(adapter_version),
    ]
}

/// What the planner needs to know about a decoded record, independent of its signal.
pub trait ChunkRecord {
    /// Where the record came from in the Buffer (drives ordering and the manifest guard).
    fn coords(&self) -> &RowSourceCoordinates;
    /// Approximate encoded size, for the byte-aware chunker.
    fn approx_size_bytes(&self) -> usize;
}

/// Identifies an adapter in tokens/fingerprints and in metric labels.
#[derive(Debug, Clone, Copy)]
pub struct AdapterKind {
    /// Versioned tag mixed into the chunking fingerprint, e.g. `otlp-traces-clickhouse-adapter:v1`.
    pub tag: &'static str,
    /// Signal label for metrics, e.g. `traces`.
    pub signal: &'static str,
}

/// Plan insert chunks for `records`.
///
/// * `route` picks the destination table and column list.
/// * `row` renders the record as column values matching `route(..).columns`.
pub fn plan_chunks<'c, R: ChunkRecord>(
    identity: &CommitIdentity,
    mut records: Vec<R>,
    cfg: &'c CommonAdapterConfig,
    kind: AdapterKind,
    route: impl Fn(&R) -> Route<'c>,
    row: impl Fn(&R) -> Vec<RowValue>,
) -> AdapterResult<Vec<InsertChunk>> {
    // RFC 0002 section "Runtime/Sink Boundary": the source range in the token
    // comes from the runtime's CommitIdentity, never from per-record fields.
    let low = identity.range.low;
    let high = identity.range.high;

    if records.is_empty() {
        return Ok(Vec::new());
    }

    records.sort_by_key(|r| {
        let c = r.coords();
        (c.buffer_sequence, c.entry_index, c.record_index)
    });

    // Fail closed if a commit ever mixes manifests: tokens would collide.
    let manifest_path = records[0].coords().manifest_path.clone();
    if let Some(other) = records
        .iter()
        .map(R::coords)
        .find(|c| c.manifest_path != manifest_path)
    {
        return Err(AdapterError::Plan(format!(
            "commit group mixes manifest paths: first record has {manifest_path}, found {}",
            other.manifest_path,
        )));
    }

    // Group by table; BTreeMap gives a deterministic table order.
    let mut groups: BTreeMap<&str, (&'static [&'static str], Vec<&R>)> = BTreeMap::new();
    for rec in &records {
        let r = route(rec);
        groups
            .entry(r.table)
            .or_insert_with(|| (r.columns, Vec::new()))
            .1
            .push(rec);
    }

    let max_rows = cfg.max_chunk_rows.max(1);
    let max_bytes = cfg.max_chunk_bytes.max(1);
    let mut chunks = Vec::new();

    for (table, (columns, group)) in groups {
        let fingerprint = chunking_fingerprint(kind.tag, cfg, table);
        let mut current: Vec<&R> = Vec::new();
        let mut current_bytes = 0usize;
        let mut chunk_index = 0u32;

        let mut emit = |slice: &[&R], chunk_index: u32| {
            let token = build_token(
                &manifest_path,
                cfg,
                table,
                &fingerprint,
                low,
                high,
                chunk_index,
            );
            chunks.push(InsertChunk {
                database: cfg.database.clone(),
                table: table.to_string(),
                columns: columns.to_vec(),
                rows: slice.iter().map(|r| row(r)).collect(),
                settings: ClickHouseSettings {
                    insert_quorum: cfg.insert_quorum.clone(),
                    insert_deduplication_token: token.clone(),
                    apply_deduplication_token: cfg.apply_deduplication_token,
                },
                idempotency_token: token,
                chunk_index,
                observability_labels: vec![
                    ("table", table.to_string()),
                    ("signal", kind.signal.to_string()),
                ],
            });
        };

        for rec in group {
            let rec_bytes = rec.approx_size_bytes();
            if !current.is_empty()
                && (current.len() + 1 > max_rows || current_bytes + rec_bytes > max_bytes)
            {
                emit(&current, chunk_index);
                chunk_index += 1;
                current.clear();
                current_bytes = 0;
            }
            current.push(rec);
            current_bytes += rec_bytes;
        }
        if !current.is_empty() {
            emit(&current, chunk_index);
        }
    }
    Ok(chunks)
}

#[cfg(test)]
mod tests {
    use super::*;
    use opendata_ingest_runtime::identity::{SchemaVersion, SequenceRange};
    use opendata_ingest_runtime::sink::SinkId;
    use opendata_ingest_runtime::source::SourceId;

    #[derive(Clone)]
    struct Rec {
        src: RowSourceCoordinates,
        table: &'static str,
    }

    const COLS: &[&str] = &["a"];

    fn rec(seq: u64, entry: u32, idx: u32, table: &'static str) -> Rec {
        Rec {
            src: RowSourceCoordinates {
                buffer_sequence: seq,
                entry_index: entry,
                record_index: idx,
                manifest_path: "m".into(),
                data_path: "d".into(),
                ingestion_time_ms: 0,
            },
            table,
        }
    }

    fn identity(low: u64, high: u64) -> CommitIdentity {
        CommitIdentity {
            source: SourceId::from("s"),
            sink: SinkId::from("k"),
            range: SequenceRange::new(low, high),
            schema_version: SchemaVersion(1),
        }
    }

    impl ChunkRecord for Rec {
        fn coords(&self) -> &RowSourceCoordinates {
            &self.src
        }
        fn approx_size_bytes(&self) -> usize {
            10
        }
    }

    fn plan(records: Vec<Rec>, cfg: &CommonAdapterConfig) -> AdapterResult<Vec<InsertChunk>> {
        plan_chunks(
            &identity(5, 7),
            records,
            cfg,
            AdapterKind {
                tag: "test:v1",
                signal: "test",
            },
            |r| Route {
                table: r.table,
                columns: COLS,
            },
            |r| {
                vec![RowValue::UInt64(
                    r.src.buffer_sequence * 100 + r.src.record_index as u64,
                )]
            },
        )
    }

    #[test]
    fn empty_input_yields_no_chunks() {
        assert!(
            plan(vec![], &CommonAdapterConfig::default())
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn same_input_in_any_order_yields_identical_chunks_and_tokens() {
        let cfg = CommonAdapterConfig::default();
        let a = plan(
            vec![rec(5, 0, 0, "t"), rec(5, 0, 1, "t"), rec(6, 0, 0, "t")],
            &cfg,
        )
        .unwrap();
        let b = plan(
            vec![rec(6, 0, 0, "t"), rec(5, 0, 1, "t"), rec(5, 0, 0, "t")],
            &cfg,
        )
        .unwrap();
        assert_eq!(a.len(), b.len());
        for (x, y) in a.iter().zip(&b) {
            assert_eq!(x.idempotency_token, y.idempotency_token);
            assert_eq!(x.rows.len(), y.rows.len());
            for (rx, ry) in x.rows.iter().zip(&y.rows) {
                assert_eq!(format!("{rx:?}"), format!("{ry:?}"));
            }
        }
    }

    #[test]
    fn token_follows_rfc_format() {
        let cfg = CommonAdapterConfig::default();
        let chunks = plan(vec![rec(5, 0, 0, "tbl")], &cfg).unwrap();
        let fp = chunking_fingerprint("test:v1", &cfg, "tbl");
        assert_eq!(
            chunks[0].idempotency_token,
            format!("m:otel.tbl:5-7:1:{fp}:0")
        );
        assert_eq!(
            chunks[0].settings.insert_deduplication_token,
            chunks[0].idempotency_token
        );
    }

    #[test]
    fn splits_by_row_limit_with_sequential_chunk_indexes() {
        let cfg = CommonAdapterConfig {
            max_chunk_rows: 2,
            ..Default::default()
        };
        let recs = (0..5).map(|i| rec(5, 0, i, "t")).collect();
        let chunks = plan(recs, &cfg).unwrap();
        let sizes: Vec<_> = chunks.iter().map(|c| c.rows.len()).collect();
        assert_eq!(sizes, vec![2, 2, 1]);
        let idx: Vec<_> = chunks.iter().map(|c| c.chunk_index).collect();
        assert_eq!(idx, vec![0, 1, 2]);
        let tokens: std::collections::BTreeSet<_> =
            chunks.iter().map(|c| c.idempotency_token.clone()).collect();
        assert_eq!(tokens.len(), 3, "tokens must be unique within a commit");
    }

    #[test]
    fn splits_by_byte_limit() {
        let cfg = CommonAdapterConfig {
            max_chunk_bytes: 25, // 10 bytes per record => 2 per chunk
            ..Default::default()
        };
        let recs = (0..5).map(|i| rec(5, 0, i, "t")).collect();
        let sizes: Vec<_> = plan(recs, &cfg)
            .unwrap()
            .iter()
            .map(|c| c.rows.len())
            .collect();
        assert_eq!(sizes, vec![2, 2, 1]);
    }

    #[test]
    fn groups_by_table_in_deterministic_order_and_conserves_rows() {
        let cfg = CommonAdapterConfig::default();
        let recs = vec![
            rec(5, 0, 0, "zeta"),
            rec(5, 0, 1, "alpha"),
            rec(5, 0, 2, "zeta"),
        ];
        let chunks = plan(recs, &cfg).unwrap();
        let tables: Vec<_> = chunks.iter().map(|c| c.table.as_str()).collect();
        assert_eq!(tables, vec!["alpha", "zeta"]);
        assert_eq!(chunks.iter().map(|c| c.rows.len()).sum::<usize>(), 3);
        // chunk_index restarts per table, but tokens differ because the table is in the token.
        assert_ne!(chunks[0].idempotency_token, chunks[1].idempotency_token);
    }

    #[test]
    fn mixed_manifest_paths_fail_closed() {
        let mut other = rec(5, 0, 1, "t");
        other.src.manifest_path = "other".into();
        let err = plan(
            vec![rec(5, 0, 0, "t"), other],
            &CommonAdapterConfig::default(),
        )
        .unwrap_err();
        assert!(err.to_string().contains("mixes manifest paths"), "{err}");
    }

    #[test]
    fn config_changes_change_the_fingerprint() {
        let a = CommonAdapterConfig::default();
        let b = CommonAdapterConfig {
            max_chunk_rows: 1,
            ..Default::default()
        };
        assert_ne!(
            chunking_fingerprint("t", &a, "x"),
            chunking_fingerprint("t", &b, "x")
        );
    }
}
