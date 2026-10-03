use anyhow::Context;
use contract::DEFAULT_BACKEND_ADDR;

async fn shutdown_signal() {
    use tokio::signal::unix::{SignalKind, signal};
    let mut term = signal(SignalKind::terminate()).expect("install SIGTERM handler");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = term.recv() => {}
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = telemetry::init("backend");
    let addr = std::env::var("BACKEND_ADDR").unwrap_or_else(|_| DEFAULT_BACKEND_ADDR.to_string());
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .with_context(|| format!("bind {addr}"))?;
    // Not a tracing event: startup chatter must not become a log record.
    eprintln!(
        "backend listening on {addr} (otlp export: {})",
        telemetry.exporting()
    );
    backend::serve(listener, shutdown_signal()).await?;
    tokio::task::spawn_blocking(|| telemetry.shutdown()).await?;
    Ok(())
}
