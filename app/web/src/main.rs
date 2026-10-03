use anyhow::Context;
use contract::{DEFAULT_BACKEND_URL, DEFAULT_WEB_ADDR};

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
    let telemetry = telemetry::init("web");
    let addr = std::env::var("WEB_ADDR").unwrap_or_else(|_| DEFAULT_WEB_ADDR.to_string());
    let backend_url =
        std::env::var("BACKEND_URL").unwrap_or_else(|_| DEFAULT_BACKEND_URL.to_string());
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .with_context(|| format!("bind {addr}"))?;
    // Not a tracing event: startup chatter must not become a log record.
    eprintln!(
        "web listening on {addr}, backend {backend_url} (otlp export: {})",
        telemetry.exporting()
    );
    web::serve(listener, &backend_url, shutdown_signal()).await?;
    tokio::task::spawn_blocking(|| telemetry.shutdown()).await?;
    Ok(())
}
