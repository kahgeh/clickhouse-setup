use std::process::ExitCode;

use anyhow::Context;
use clap::Parser;
use contract::parse_counts;

#[derive(Parser, Debug)]
#[command(about = "Deterministic load generator that writes a ground-truth JSON file")]
struct Args {
    /// Base URL of the web service.
    #[arg(long, default_value = "http://localhost:8081")]
    base_url: String,
    /// Test run id (sent as x-test-run-id). Random UUID when omitted.
    #[arg(long)]
    run_id: Option<String>,
    /// Requests per endpoint, e.g. ok=100,slow=20,error=20,exception=5,chain=50,chain-fail=10,checkout=20
    #[arg(
        long,
        default_value = "ok=100,slow=20,error=20,exception=5,chain=50,chain-fail=10,checkout=20"
    )]
    counts: String,
    /// Delay requested from /slow, in milliseconds.
    #[arg(long, default_value_t = 200)]
    slow_ms: u64,
    /// Maximum in-flight requests.
    #[arg(long, default_value_t = 4)]
    concurrency: usize,
    /// Output path of the ground truth JSON.
    #[arg(long, default_value = "ground_truth.json")]
    out: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<ExitCode> {
    let args = Args::parse();
    let cfg = loadgen::Config {
        base_url: args.base_url,
        run_id: args
            .run_id
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
        counts: parse_counts(&args.counts)?,
        slow_ms: args.slow_ms,
        concurrency: args.concurrency,
    };
    let outcome = loadgen::run(&cfg).await?;
    let json = serde_json::to_string_pretty(&outcome.truth)?;
    std::fs::write(&args.out, json).with_context(|| format!("write {}", args.out))?;
    println!(
        "run_id={} requests={} wrote {}",
        outcome.truth.run_id, outcome.truth.totals.requests, args.out
    );
    if outcome.problems.is_empty() {
        return Ok(ExitCode::SUCCESS);
    }
    for p in &outcome.problems {
        eprintln!("MISMATCH: {p}");
    }
    Ok(ExitCode::FAILURE)
}
