//! In-process end-to-end test: backend + web on random ports, telemetry without an endpoint.

use std::collections::BTreeSet;

use contract::{Endpoint, HEADER_RUN_ID, HEADER_TRACE_ID, is_trace_id, parse_counts};
use tokio::net::TcpListener;

async fn start() -> String {
    let backend_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let backend_addr = backend_listener.local_addr().unwrap();
    let web_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let web_addr = web_listener.local_addr().unwrap();
    tokio::spawn(backend::serve(backend_listener, std::future::pending()));
    let backend_url = format!("http://{backend_addr}");
    tokio::spawn(
        async move { web::serve(web_listener, &backend_url, std::future::pending()).await },
    );
    format!("http://{web_addr}")
}

#[tokio::test]
async fn every_endpoint_has_expected_status_and_trace_id() {
    assert!(
        std::env::var(telemetry::ENDPOINT_ENV).is_err(),
        "test must run with telemetry export disabled"
    );
    let base = start().await;
    let client = reqwest::Client::new();

    for e in Endpoint::ALL {
        let url = match e {
            Endpoint::Slow => format!("{base}/slow?ms=30"),
            _ => format!("{base}{}", e.path()),
        };
        let started = std::time::Instant::now();
        let r = client
            .get(&url)
            .header(HEADER_RUN_ID, "t1")
            .send()
            .await
            .unwrap();
        assert_eq!(r.status().as_u16(), e.expected_status(), "{}", e.name());
        let trace = r
            .headers()
            .get(HEADER_TRACE_ID)
            .unwrap_or_else(|| panic!("{} lacks x-trace-id", e.name()))
            .to_str()
            .unwrap()
            .to_string();
        assert!(is_trace_id(&trace), "{}: {trace}", e.name());
        if e == Endpoint::Slow {
            assert!(started.elapsed().as_millis() >= 30);
        }
    }

    assert_eq!(
        client
            .get(format!("{base}/chain"))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    assert_eq!(
        client
            .get(format!("{base}/chain-fail"))
            .send()
            .await
            .unwrap()
            .status(),
        502
    );

    let health = client.get(format!("{base}/health")).send().await.unwrap();
    assert_eq!(health.status(), 200);
    assert!(health.headers().get(HEADER_TRACE_ID).is_none());

    let flush = client
        .post(format!("{base}/admin/flush"))
        .send()
        .await
        .unwrap();
    assert_eq!(flush.status(), 200);
    assert!(flush.headers().get(HEADER_TRACE_ID).is_none());
}

#[tokio::test]
async fn trace_ids_are_unique_per_request() {
    let base = start().await;
    let client = reqwest::Client::new();
    let mut seen = BTreeSet::new();
    for _ in 0..5 {
        let r = client.get(format!("{base}/ok")).send().await.unwrap();
        seen.insert(r.headers()[HEADER_TRACE_ID].to_str().unwrap().to_string());
    }
    assert_eq!(seen.len(), 5);
}

#[tokio::test]
async fn loadgen_ground_truth_matches_contract() {
    let base = start().await;
    let cfg = loadgen::Config {
        base_url: base.clone(),
        run_id: "run-x".into(),
        counts: parse_counts("ok=6,slow=2,error=2,exception=1,chain=4,chain-fail=3,checkout=2")
            .unwrap(),
        slow_ms: 10,
        concurrency: 3,
    };
    let out = loadgen::run(&cfg).await.unwrap();
    assert!(out.problems.is_empty(), "{:?}", out.problems);
    let gt = out.truth;
    assert_eq!(gt.run_id, "run-x");
    assert_eq!(gt.base_url, base);
    assert!(gt.ended_at_unix_nano >= gt.started_at_unix_nano);
    assert_eq!(gt.totals.requests, 20);
    assert_eq!(gt.totals.expected_spans, 6 + 2 + 2 + 1 + 16 + 9 + 44);
    assert_eq!(gt.totals.expected_logs, 6 + 2 + 2 + 1 + 8 + 6 + 4);
    assert_eq!(gt.totals.expected_http_5xx, 6);
    assert!((gt.totals.expected_availability - 14.0 / 20.0).abs() < 1e-12);

    let chain_fail = &gt.per_endpoint["chain-fail"];
    assert_eq!(chain_fail.count, 3);
    assert_eq!(chain_fail.expected_status, 502);
    assert_eq!(chain_fail.actual_status_counts["502"], 3);
    assert_eq!(chain_fail.expected_spans_each, 3);
    assert_eq!(chain_fail.expected_logs_each, 2);

    let mut all = BTreeSet::new();
    for e in Endpoint::ALL {
        let ids = &gt.trace_ids[e.name()];
        assert_eq!(ids.len() as u64, gt.per_endpoint[e.name()].count);
        all.extend(ids.iter().cloned());
    }
    assert_eq!(all.len(), 20);

    // The ground truth round-trips through JSON.
    let json = serde_json::to_string(&gt).unwrap();
    assert_eq!(
        serde_json::from_str::<contract::GroundTruth>(&json).unwrap(),
        gt
    );
}

#[tokio::test]
async fn loadgen_reports_status_mismatch() {
    // Point loadgen at a server that answers 404 for everything.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        serve_404(listener).await;
    });
    let cfg = loadgen::Config {
        base_url: format!("http://{addr}"),
        run_id: "r".into(),
        counts: parse_counts("ok=2").unwrap(),
        slow_ms: 1,
        concurrency: 1,
    };
    let out = loadgen::run(&cfg).await.unwrap();
    assert!(!out.problems.is_empty());
    assert_eq!(out.truth.per_endpoint["ok"].actual_status_counts["404"], 2);
}

/// Minimal HTTP/1.1 responder returning 404 with no body.
async fn serve_404(listener: TcpListener) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    loop {
        let Ok((mut sock, _)) = listener.accept().await else {
            return;
        };
        tokio::spawn(async move {
            let mut buf = [0u8; 4096];
            while let Ok(n) = sock.read(&mut buf).await {
                if n == 0 {
                    break;
                }
                let resp = "HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n";
                if sock.write_all(resp.as_bytes()).await.is_err() {
                    break;
                }
            }
        });
    }
}
