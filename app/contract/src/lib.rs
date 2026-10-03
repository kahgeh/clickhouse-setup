//! Shared contract between the test services and the load generator.
//!
//! The per-endpoint expectation table in [`Endpoint`] is the single source of truth for
//! the amount of telemetry each request must produce.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Generated gRPC types for the backend service.
pub mod pb {
    tonic::include_proto!("sre.backend");
}

/// Request header carrying the test run id (HTTP and gRPC metadata).
pub const HEADER_RUN_ID: &str = "x-test-run-id";
/// Response header carrying the 32-hex trace id of the request's server span.
pub const HEADER_TRACE_ID: &str = "x-trace-id";
/// Run id used when the request carries none.
pub const DEFAULT_RUN_ID: &str = "none";
/// Default web listen address.
pub const DEFAULT_WEB_ADDR: &str = "0.0.0.0:8081";
/// Default backend listen address.
pub const DEFAULT_BACKEND_ADDR: &str = "0.0.0.0:50051";
/// Default backend URL used by web.
pub const DEFAULT_BACKEND_URL: &str = "http://localhost:50051";
/// Default `/slow` delay in milliseconds.
pub const DEFAULT_SLOW_MS: u64 = 200;
/// Path of the admin flush endpoint.
pub const FLUSH_PATH: &str = "/admin/flush";
/// Path of the health endpoint.
pub const HEALTH_PATH: &str = "/health";

#[derive(Debug, thiserror::Error)]
pub enum ContractError {
    #[error("unknown endpoint name `{0}`")]
    UnknownEndpoint(String),
    #[error("invalid counts entry `{0}` (expected name=number)")]
    BadCount(String),
}

/// The telemetry-producing endpoints of the web service.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Endpoint {
    Ok,
    Slow,
    Error,
    Exception,
    Chain,
    ChainFail,
    Checkout,
}

impl Endpoint {
    /// Fixed order, used for deterministic interleaving.
    pub const ALL: [Endpoint; 7] = [
        Endpoint::Ok,
        Endpoint::Slow,
        Endpoint::Error,
        Endpoint::Exception,
        Endpoint::Chain,
        Endpoint::ChainFail,
        Endpoint::Checkout,
    ];

    /// Name used in counts, ground truth keys and the CLI.
    pub fn name(self) -> &'static str {
        match self {
            Endpoint::Ok => "ok",
            Endpoint::Slow => "slow",
            Endpoint::Error => "error",
            Endpoint::Exception => "exception",
            Endpoint::Chain => "chain",
            Endpoint::ChainFail => "chain-fail",
            Endpoint::Checkout => "checkout",
        }
    }

    pub fn from_name(name: &str) -> Result<Endpoint, ContractError> {
        Endpoint::ALL
            .into_iter()
            .find(|e| e.name() == name)
            .ok_or_else(|| ContractError::UnknownEndpoint(name.to_string()))
    }

    /// HTTP route (also the `http.route` attribute).
    pub fn path(self) -> &'static str {
        match self {
            Endpoint::Ok => "/ok",
            Endpoint::Slow => "/slow",
            Endpoint::Error => "/error",
            Endpoint::Exception => "/exception",
            Endpoint::Chain => "/chain",
            Endpoint::ChainFail => "/chain-fail",
            Endpoint::Checkout => "/checkout",
        }
    }

    pub fn expected_status(self) -> u16 {
        match self {
            Endpoint::Ok | Endpoint::Slow | Endpoint::Chain | Endpoint::Checkout => 200,
            Endpoint::Error | Endpoint::Exception => 500,
            Endpoint::ChainFail => 502,
        }
    }

    /// Number of spans (across all services) one request produces.
    pub fn expected_spans(self) -> u64 {
        match self {
            Endpoint::Ok | Endpoint::Slow | Endpoint::Error | Endpoint::Exception => 1,
            Endpoint::Chain => 4,
            Endpoint::ChainFail => 3,
            // web: server + 9 function spans + client; backend: server + 10 function spans
            // (call trees in `web::checkout` and `backend::checkout`).
            Endpoint::Checkout => 22,
        }
    }

    /// Number of log records (across all services) one request produces.
    pub fn expected_logs(self) -> u64 {
        match self {
            Endpoint::Ok | Endpoint::Slow | Endpoint::Error | Endpoint::Exception => 1,
            Endpoint::Chain | Endpoint::ChainFail | Endpoint::Checkout => 2,
        }
    }

    /// Whether the expected status is a server error (>= 500).
    pub fn is_5xx(self) -> bool {
        self.expected_status() >= 500
    }
}

/// Parses `ok=100,slow=20,...` into counts, in [`Endpoint::ALL`] order; unspecified endpoints are 0.
pub fn parse_counts(s: &str) -> Result<BTreeMap<Endpoint, u64>, ContractError> {
    let mut out: BTreeMap<Endpoint, u64> = Endpoint::ALL.into_iter().map(|e| (e, 0)).collect();
    for part in s.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        let (name, n) = part
            .split_once('=')
            .ok_or_else(|| ContractError::BadCount(part.to_string()))?;
        let n: u64 = n
            .trim()
            .parse()
            .map_err(|_| ContractError::BadCount(part.to_string()))?;
        out.insert(Endpoint::from_name(name.trim())?, n);
    }
    Ok(out)
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EndpointTruth {
    pub count: u64,
    pub expected_status: u16,
    pub actual_status_counts: BTreeMap<String, u64>,
    pub expected_spans_each: u64,
    pub expected_logs_each: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Totals {
    pub requests: u64,
    pub expected_spans: u64,
    pub expected_logs: u64,
    pub expected_http_5xx: u64,
    /// non-5xx / total (1.0 when there are no requests).
    pub expected_availability: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroundTruth {
    pub run_id: String,
    pub started_at_unix_nano: u64,
    pub ended_at_unix_nano: u64,
    pub base_url: String,
    pub per_endpoint: BTreeMap<String, EndpointTruth>,
    pub totals: Totals,
    pub trace_ids: BTreeMap<String, Vec<String>>,
}

/// Computes totals from the contract table (never from observed telemetry).
pub fn expected_totals(counts: &BTreeMap<Endpoint, u64>) -> Totals {
    let mut t = Totals {
        requests: 0,
        expected_spans: 0,
        expected_logs: 0,
        expected_http_5xx: 0,
        expected_availability: 1.0,
    };
    for (e, &n) in counts {
        t.requests += n;
        t.expected_spans += n * e.expected_spans();
        t.expected_logs += n * e.expected_logs();
        if e.is_5xx() {
            t.expected_http_5xx += n;
        }
    }
    if t.requests > 0 {
        t.expected_availability = (t.requests - t.expected_http_5xx) as f64 / t.requests as f64;
    }
    t
}

/// 32 lowercase hex characters.
pub fn is_trace_id(s: &str) -> bool {
    s.len() == 32
        && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
        && s.bytes().any(|b| b != b'0')
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_matches_contract() {
        let t: Vec<_> = Endpoint::ALL
            .iter()
            .map(|e| {
                (
                    e.name(),
                    e.path(),
                    e.expected_status(),
                    e.expected_spans(),
                    e.expected_logs(),
                )
            })
            .collect();
        assert_eq!(
            t,
            vec![
                ("ok", "/ok", 200, 1, 1),
                ("slow", "/slow", 200, 1, 1),
                ("error", "/error", 500, 1, 1),
                ("exception", "/exception", 500, 1, 1),
                ("chain", "/chain", 200, 4, 2),
                ("chain-fail", "/chain-fail", 502, 3, 2),
                ("checkout", "/checkout", 200, 22, 2),
            ]
        );
    }

    #[test]
    fn names_round_trip() {
        for e in Endpoint::ALL {
            assert_eq!(Endpoint::from_name(e.name()).unwrap(), e);
        }
        assert!(Endpoint::from_name("nope").is_err());
    }

    #[test]
    fn counts_parse() {
        let c = parse_counts("ok=100, slow=20,chain-fail=10").unwrap();
        assert_eq!(c[&Endpoint::Ok], 100);
        assert_eq!(c[&Endpoint::Slow], 20);
        assert_eq!(c[&Endpoint::ChainFail], 10);
        assert_eq!(c[&Endpoint::Error], 0);
        assert!(parse_counts("ok").is_err());
        assert!(parse_counts("ok=x").is_err());
        assert!(parse_counts("bogus=1").is_err());
    }

    #[test]
    fn totals_for_default_mix() {
        let c =
            parse_counts("ok=100,slow=20,error=20,exception=5,chain=50,chain-fail=10,checkout=20")
                .unwrap();
        let t = expected_totals(&c);
        assert_eq!(t.requests, 225);
        assert_eq!(t.expected_spans, 100 + 20 + 20 + 5 + 200 + 30 + 440);
        assert_eq!(t.expected_logs, 100 + 20 + 20 + 5 + 100 + 20 + 40);
        assert_eq!(t.expected_http_5xx, 35);
        assert!((t.expected_availability - 190.0 / 225.0).abs() < 1e-12);
    }

    #[test]
    fn empty_totals_are_fully_available() {
        let t = expected_totals(&parse_counts("").unwrap());
        assert_eq!(t.requests, 0);
        assert_eq!(t.expected_availability, 1.0);
    }

    #[test]
    fn trace_id_format() {
        assert!(is_trace_id("0123456789abcdef0123456789abcdef"));
        assert!(!is_trace_id("0123456789ABCDEF0123456789abcdef"));
        assert!(!is_trace_id("abc"));
        assert!(!is_trace_id("00000000000000000000000000000000"));
    }
}
