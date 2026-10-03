//! Deterministic load generator. Expectations come from the `contract` crate only.

use std::collections::BTreeMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::Context;
use contract::{
    Endpoint, EndpointTruth, FLUSH_PATH, GroundTruth, HEADER_RUN_ID, HEADER_TRACE_ID,
    expected_totals, is_trace_id,
};
use futures::StreamExt;

#[derive(Debug, Clone)]
pub struct Config {
    pub base_url: String,
    pub run_id: String,
    pub counts: BTreeMap<Endpoint, u64>,
    pub slow_ms: u64,
    pub concurrency: usize,
}

/// Result of a run: the ground truth plus human-readable problems (empty when all good).
#[derive(Debug)]
pub struct Outcome {
    pub truth: GroundTruth,
    pub problems: Vec<String>,
}

/// Builds the request list, interleaved round-robin by endpoint in [`Endpoint::ALL`] order.
pub fn build_plan(counts: &BTreeMap<Endpoint, u64>) -> Vec<Endpoint> {
    let mut remaining: Vec<(Endpoint, u64)> = Endpoint::ALL
        .into_iter()
        .map(|e| (e, counts.get(&e).copied().unwrap_or(0)))
        .collect();
    let mut plan = Vec::new();
    while remaining.iter().any(|(_, n)| *n > 0) {
        for (e, n) in remaining.iter_mut().filter(|(_, n)| *n > 0) {
            plan.push(*e);
            *n -= 1;
        }
    }
    plan
}

fn now_nanos() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos() as u64)
}

struct Sent {
    endpoint: Endpoint,
    /// `Ok((status, trace_id))` or a transport error description.
    result: Result<(u16, Option<String>), String>,
}

pub async fn run(cfg: &Config) -> anyhow::Result<Outcome> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .context("build http client")?;
    let base = cfg.base_url.trim_end_matches('/').to_string();
    let plan = build_plan(&cfg.counts);
    let started_at = now_nanos();

    // `buffered` keeps results in plan order, so trace id lists are deterministic.
    let sent: Vec<Sent> = futures::stream::iter(plan)
        .map(|endpoint| {
            let client = client.clone();
            let url = match endpoint {
                Endpoint::Slow => format!("{base}{}?ms={}", endpoint.path(), cfg.slow_ms),
                _ => format!("{base}{}", endpoint.path()),
            };
            let run_id = cfg.run_id.clone();
            async move {
                let result = client
                    .get(&url)
                    .header(HEADER_RUN_ID, run_id)
                    .send()
                    .await
                    .map(|r| {
                        let trace = r
                            .headers()
                            .get(HEADER_TRACE_ID)
                            .and_then(|v| v.to_str().ok())
                            .map(str::to_string);
                        (r.status().as_u16(), trace)
                    })
                    .map_err(|e| e.to_string());
                Sent { endpoint, result }
            }
        })
        .buffered(cfg.concurrency.max(1))
        .collect()
        .await;

    let mut problems = Vec::new();
    match client
        .post(format!("{base}{FLUSH_PATH}"))
        .header(HEADER_RUN_ID, &cfg.run_id)
        .send()
        .await
    {
        Ok(r) if r.status().is_success() => {}
        Ok(r) => problems.push(format!("{FLUSH_PATH} returned {}", r.status())),
        Err(e) => problems.push(format!("{FLUSH_PATH} failed: {e}")),
    }
    let ended_at = now_nanos();

    let mut per_endpoint: BTreeMap<String, EndpointTruth> = BTreeMap::new();
    let mut trace_ids: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for e in Endpoint::ALL {
        per_endpoint.insert(
            e.name().to_string(),
            EndpointTruth {
                count: cfg.counts.get(&e).copied().unwrap_or(0),
                expected_status: e.expected_status(),
                actual_status_counts: BTreeMap::new(),
                expected_spans_each: e.expected_spans(),
                expected_logs_each: e.expected_logs(),
            },
        );
        trace_ids.insert(e.name().to_string(), Vec::new());
    }

    for s in sent {
        let name = s.endpoint.name().to_string();
        let truth = per_endpoint
            .get_mut(&name)
            .expect("endpoint pre-registered");
        match s.result {
            Ok((status, trace)) => {
                *truth
                    .actual_status_counts
                    .entry(status.to_string())
                    .or_default() += 1;
                if status != s.endpoint.expected_status() {
                    problems.push(format!(
                        "{name}: expected status {}, got {status}",
                        s.endpoint.expected_status()
                    ));
                }
                match trace {
                    Some(t) if is_trace_id(&t) => {
                        trace_ids.get_mut(&name).expect("pre-registered").push(t)
                    }
                    other => problems.push(format!("{name}: bad {HEADER_TRACE_ID}: {other:?}")),
                }
            }
            Err(err) => {
                *truth
                    .actual_status_counts
                    .entry("transport_error".to_string())
                    .or_default() += 1;
                problems.push(format!("{name}: transport error: {err}"));
            }
        }
    }

    let truth = GroundTruth {
        run_id: cfg.run_id.clone(),
        started_at_unix_nano: started_at,
        ended_at_unix_nano: ended_at,
        base_url: cfg.base_url.clone(),
        per_endpoint,
        totals: expected_totals(&cfg.counts),
        trace_ids,
    };
    Ok(Outcome { truth, problems })
}

#[cfg(test)]
mod tests {
    use super::*;
    use contract::parse_counts;

    #[test]
    fn plan_is_round_robin_and_deterministic() {
        let c = parse_counts("ok=3,slow=1,chain=2").unwrap();
        let names: Vec<_> = build_plan(&c).iter().map(|e| e.name()).collect();
        assert_eq!(names, ["ok", "slow", "chain", "ok", "chain", "ok"]);
        assert_eq!(build_plan(&c), build_plan(&c));
    }

    #[test]
    fn plan_length_matches_counts() {
        let c =
            parse_counts("ok=100,slow=20,error=20,exception=5,chain=50,chain-fail=10,checkout=20")
                .unwrap();
        assert_eq!(build_plan(&c).len(), 225);
        assert!(build_plan(&parse_counts("").unwrap()).is_empty());
    }
}
