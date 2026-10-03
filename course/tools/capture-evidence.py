#!/usr/bin/env python3
"""Regenerate course/evidence/ from the running stack and from stack/verify/out/ (standard library only).

  capture-evidence.py [--screens] [--war-stories] [--chaos-replays]

Always: copies the latest reports, samples ClickHouse (counts, one /chain trace with its logs, metric rows),
snapshots compose status, the DDL, the cost comparison, and the alert webhook deliveries.
--screens        screenshots of the live HyperDX UI through tools/shot.mjs (headless Chrome, DevTools Protocol)
--war-stories    reproduces two upstream problems with the unpatched upstream collector image (traces unsupported;
                 data dropped on an object-store failure) and stores their real output. Briefly stops the local S3.
--chaos-replays  runs the SIGKILL scenario and stores how many replayed inserts ClickHouse dropped.
Every item gets a caption with source, command and capture time in evidence/manifest.json.
"""
import argparse
import base64
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
COURSE = os.path.dirname(HERE)
ROOT = os.path.dirname(COURSE)
EVID = os.path.join(COURSE, "evidence")
OUT = os.path.join(ROOT, "stack", "verify", "out")
CH = "http://localhost:8123/"
CH_AUTH = "Basic " + base64.b64encode(b"ingestor:ingestor").decode()
MANIFEST = {}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def note(name, source, command, **extra):
    MANIFEST[name] = {"source": source, "command": command, "captured_at": now(), **extra}


def write(name, data):
    path = os.path.join(EVID, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if isinstance(data, (dict, list)):
            json.dump(data, f, indent=2, default=str)
        else:
            f.write(data)
    return path


def sh(*cmd, timeout=300, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr[-400:]}")
    return r


def ch(sql):
    req = urllib.request.Request(CH + "?default_format=JSONEachRow", data=sql.encode(), headers={"Authorization": CH_AUTH}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return [json.loads(x) for x in r.read().decode().splitlines() if x.strip()]


def latest(pattern, ok_only=False, min_checks=0):
    best = None
    for path in sorted(glob.glob(os.path.join(OUT, pattern, "report.json")), key=os.path.getmtime, reverse=True):
        try:
            rep = json.load(open(path))
        except ValueError:
            continue
        if min_checks and len(rep.get("checks", [])) < min_checks:
            continue
        if ok_only and rep.get("failed"):
            continue
        best = (path, rep)
        break
    return best


def reports():
    # verification: newest fully-passing run with the full check set
    v = latest("*", ok_only=True, min_checks=31)
    if v:
        path, rep = v
        gt = os.path.join(os.path.dirname(path), "ground_truth.json")
        write("verify-report.json", rep)
        if os.path.exists(gt):
            write("ground-truth.json", json.load(open(gt)))
        note("verify-report.json", path, "python3 stack/verify/verify.py", run_id=rep["run_id"],
             passed=rep["passed"], failed=rep["failed"])
    # selftest: newest baseline + its seven mutation runs
    bases = sorted(glob.glob(os.path.join(OUT, "selftest-*-base")), key=os.path.getmtime, reverse=True)
    if bases:
        tag = os.path.basename(bases[0]).split("-")[1]
        sys.path.insert(0, os.path.join(ROOT, "stack", "verify"))
        import selftest  # noqa: E402
        rows = []
        for i, (name, _apply, expect) in enumerate(selftest.MUTATIONS):
            p = os.path.join(OUT, f"selftest-{tag}-m{i}", "report.json")
            if os.path.exists(p):
                rep = json.load(open(p))
                failed = sorted({c["id"] for c in rep["checks"] if not c["ok"]})
                rows.append({"mutation": name, "must_fail": sorted(expect), "failed": failed,
                             "detected": set(expect) <= set(failed)})
        base = json.load(open(os.path.join(bases[0], "report.json")))
        write("selftest.json", {"tag": tag, "baseline_failed": [c["id"] for c in base["checks"] if not c["ok"]], "mutations": rows})
        note("selftest.json", f"stack/verify/out/selftest-{tag}-*", "python3 stack/verify/selftest.py", detected=sum(r["detected"] for r in rows), total=len(rows))
    # chaos: newest report per scenario
    scen = {}
    for path in sorted(glob.glob(os.path.join(OUT, "chaos-*", "report.json")), key=os.path.getmtime):
        name = os.path.basename(os.path.dirname(path)).rsplit("-", 1)[0]
        rep = json.load(open(path))
        scen[name] = {"run_id": rep["run_id"], "passed": rep["passed"], "failed": rep["failed"], "checks": len(rep["checks"])}
    write("chaos.json", scen)
    note("chaos.json", "stack/verify/out/chaos-*", "python3 stack/verify/chaos.py", scenarios=len(scen))
    # alerts: newest passing run
    a = latest("alerts-*", ok_only=True, min_checks=9)
    if a:
        write("alerts-report.json", a[1])
        note("alerts-report.json", a[0], "python3 stack/verify/alerts.py", run_id=a[1]["run_id"], passed=a[1]["passed"])
    try:
        deliveries = json.load(urllib.request.urlopen("http://localhost:8089/received", timeout=10))
        parsed = []
        for d in deliveries:
            try:
                b = json.loads(d["body"])
                b["received_at"] = d["received_at"]
                parsed.append(b)
            except ValueError:
                pass
        write("alert-deliveries.json", parsed)
        note("alert-deliveries.json", "webhook sink GET /received", "curl localhost:8089/received", count=len(parsed))
    except Exception as e:  # noqa: BLE001
        write("alert-deliveries.json", [])
        note("alert-deliveries.json", "webhook sink", "unavailable", error=str(e))


def samples():
    gt = os.path.join(EVID, "ground-truth.json")
    counts = {r["t"]: r["n"] for r in ch("SELECT 'otel_logs' t, count() n FROM otel.otel_logs UNION ALL SELECT 'otel_traces', count() FROM otel.otel_traces "
                                         "UNION ALL SELECT 'otel_metrics_sum', count() FROM otel.otel_metrics_sum UNION ALL SELECT 'otel_metrics_histogram', count() FROM otel.otel_metrics_histogram")}
    write("clickhouse-counts.json", counts)
    note("clickhouse-counts.json", "ClickHouse otel.*", "SELECT count() FROM each table")
    if os.path.exists(gt):
        tids = json.load(open(gt)).get("trace_ids", {}).get("chain", [])
        if tids:
            t = tids[0]
            spans = ch(f"SELECT ServiceName, SpanKind, SpanName, StatusCode, round(Duration/1e6,3) AS duration_ms, SpanId, ParentSpanId FROM otel.otel_traces WHERE TraceId='{t}' ORDER BY Timestamp")
            logs = ch(f"SELECT ServiceName, SeverityText, Body, SpanId FROM otel.otel_logs WHERE TraceId='{t}' ORDER BY Timestamp")
            write("trace-sample.json", {"trace_id": t, "spans": spans, "logs": logs})
            note("trace-sample.json", "ClickHouse otel.otel_traces / otel_logs", f"WHERE TraceId = '{t}'")
    if os.path.exists(gt):
        tids = json.load(open(gt)).get("trace_ids", {}).get("checkout", [])
        if tids:
            t = tids[0]
            spans = ch(f"SELECT ServiceName, SpanKind, SpanName, StatusCode, round(Duration/1e6,3) AS duration_ms, SpanId, ParentSpanId, "
                       f"SpanAttributes['items'] AS items, SpanAttributes['slowest_line_ms'] AS slowest_line_ms, SpanAttributes['code.function'] AS code_function "
                       f"FROM otel.otel_traces WHERE TraceId='{t}' ORDER BY Timestamp")
            write("checkout-trace-sample.json", {"trace_id": t, "spans": spans})
            note("checkout-trace-sample.json", "ClickHouse otel.otel_traces", f"WHERE TraceId = '{t}' (a /checkout request: function spans from #[trace_all])")
    met = ch("SELECT MetricName, any(ServiceName) svc, count() points, max(Value) latest_max FROM otel.otel_metrics_sum WHERE ServiceName IN ('web','backend') GROUP BY MetricName ORDER BY MetricName")
    write("metrics-sample.json", met)
    note("metrics-sample.json", "ClickHouse otel.otel_metrics_sum", "GROUP BY MetricName")
    parts = ch("SELECT table, sum(rows) AS n_rows, sum(data_compressed_bytes) AS compressed, sum(data_uncompressed_bytes) AS raw FROM system.parts WHERE database='otel' AND active GROUP BY table ORDER BY table")
    write("storage.json", parts)
    note("storage.json", "ClickHouse system.parts", "compressed vs raw per table (SYNTHETIC repetitive test data: not representative)")


def misc():
    r = sh("docker", "compose", "-p", "obs", "ps", "-a", "--format", "table {{.Service}}\t{{.Image}}\t{{.Status}}")
    write("compose-ps.txt", "\n".join(l for l in r.stdout.splitlines() if "Executing" not in l and l.strip()))
    note("compose-ps.txt", "docker compose -p obs ps -a", "docker compose -p obs ps -a")
    ddl = ""
    for f in sorted(glob.glob(os.path.join(ROOT, "stack", "clickhouse", "*.sql"))):
        ddl += f"-- {os.path.basename(f)}\n" + open(f).read() + "\n"
    write("ddl.sql", ddl)
    note("ddl.sql", "stack/clickhouse/*.sql", "cat")
    for f in ("comparison.json", "baseline.json"):
        p = os.path.join(ROOT, "cost", f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(EVID, f))
            note(f, f"cost/{f}", "python3 cost/compare.py --json cost/comparison.json")
    sh_out = sh("python3", os.path.join(ROOT, "cost", "model.py"), "--scan-tb", "2384").stdout
    write("cost-model-output.txt", sh_out)
    note("cost-model-output.txt", "cost/model.py", "python3 cost/model.py --scan-tb 2384")


def war_stories():
    SPD = os.path.join(EVID, "_tmp")
    os.makedirs(SPD, exist_ok=True)
    cfg = os.path.join(SPD, "traces.yaml")
    open(cfg, "w").write("""receivers:
  otlp: {protocols: {grpc: {endpoint: 0.0.0.0:4317}}}
exporters:
  opendata: {object_store: {type: s3, bucket: b, region: us-east-1, endpoint: "http://s3:9000"}, data_path_prefix: x/data, manifest_path: x/manifest}
service: {pipelines: {traces: {receivers: [otlp], exporters: [opendata]}}}
""")
    img = "ghcr.io/opendata-oss/otel-collector:v0.4.0"
    r = sh("docker", "run", "--rm", "--platform", "linux/amd64", "-v", f"{cfg}:/c.yaml:ro", img, "validate", "--config=/c.yaml", timeout=120)
    write("war-traces-unsupported.txt", f"$ otelcol-opendata validate  # upstream image {img}, pipeline: traces -> opendata exporter\n"
          + (r.stdout + r.stderr).strip().splitlines()[-1] + "\n")
    note("war-traces-unsupported.txt", img, "validate a traces pipeline with the opendata exporter")
    # data dropped on object-store failure with the upstream exporter (no retry)
    logs_cfg = os.path.join(SPD, "logs.yaml")
    open(logs_cfg, "w").write("""receivers:
  otlp: {protocols: {grpc: {endpoint: 0.0.0.0:4317}}}
exporters:
  opendata: {object_store: {type: s3, bucket: observability-buffer, region: us-east-1, endpoint: "http://s3:9000"}, data_path_prefix: war/data, manifest_path: war/manifest, flush_interval: 100ms}
service: {pipelines: {logs: {receivers: [otlp], exporters: [opendata]}}}
""")
    name = "war-upstream-collector"
    sh("docker", "rm", "-f", name)
    sh("docker", "run", "-d", "--name", name, "--platform", "linux/amd64", "--network", "obs_default",
       "--network-alias", "war-collector", "-e", "AWS_ACCESS_KEY_ID=localadmin", "-e", "AWS_SECRET_ACCESS_KEY=localadmin-secret",
       "-e", "AWS_REGION=us-east-1", "-v", f"{logs_cfg}:/c.yaml:ro", img, "--config=/c.yaml")
    for _ in range(40):  # wait until the (emulated amd64) collector is ready
        if "Everything is ready" in sh("docker", "logs", name).stdout + sh("docker", "logs", name).stderr:
            break
        time.sleep(1)
    sh("docker", "stop", "-t", "3", "obs-s3-1")
    sh("docker", "run", "--rm", "--network", "obs_default", "ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:latest",
       "logs", "--otlp-endpoint=war-collector:4317", "--otlp-insecure", "--logs", "300", "--rate", "0", "--workers", "1", timeout=120)
    time.sleep(9)  # longer than the exporter's 5 s call timeout: a shorter outage is absorbed by the producer's own retries
    sh("docker", "start", "obs-s3-1")
    time.sleep(2)
    lg = sh("docker", "logs", name).stderr + sh("docker", "logs", name).stdout
    lines = [l for l in lg.splitlines() if "Dropping data" in l]
    short = []
    for l in lines[:4]:
        parts = l.split("\t")
        err = l.split('"error": "')[1].split('"')[0][:230] if '"error": "' in l else ""
        dropped = l.split('"dropped_items": ')[1].split("}")[0] if '"dropped_items": ' in l else "?"
        short.append(f"{parts[0]}  error  Exporting failed. Dropping data.  dropped_items={dropped}\n    {err}")
    write("war-no-retry.txt", "# upstream collector image, object store stopped for ~9 s while 300 log records were sent\n" + "\n".join(short) + "\n")
    note("war-no-retry.txt", img, "stop S3 mid-export, read the collector log", dropping_lines=len(lines))
    sh("docker", "rm", "-f", name)
    shutil.rmtree(SPD, ignore_errors=True)


def chaos_replays(attempts=3):
    """Run the SIGKILL scenario several times: whether a kill lands mid-commit (and so causes a replay) is timing-dependent."""
    out = []
    for i in range(1, attempts + 1):
        r = sh(sys.executable, os.path.join(ROOT, "stack", "verify", "chaos.py"), "ingestor-crash", timeout=600)
        out.append(f"##### attempt {i}\n{r.stdout}")
    write("chaos-ingestor-crash.txt", "\n".join(out))
    note("chaos-ingestor-crash.txt", "stack/verify/chaos.py", f"python3 stack/verify/chaos.py ingestor-crash (x{attempts})")


def screens():
    sys.path.insert(0, os.path.join(ROOT, "stack", "hyperdx"))
    import provision  # noqa: E402
    s = provision.Session()
    s.call("POST", f"{provision.UI}/api/login/password", {"email": provision.EMAIL, "password": provision.PASSWORD})
    cookie = "connect.sid=" + s.cookies["connect.sid"]
    st = json.load(open(os.path.join(ROOT, "stack", "hyperdx", "state.json")))
    shots = [
        ("dash-availability.png", f"/dashboards/{st['dashboards']['Availability & Latency']}", 1500, 1500),
        ("dash-runlog.png", f"/dashboards/{st['dashboards']['Runlog']}", 1500, 1100),
        ("search-error-logs.png", f"/search/{st['savedSearches']['Error list: error logs']}", 1500, 900),
        ("alerts.png", "/alerts", 1500, 800),
    ]
    os.makedirs(os.path.join(EVID, "screens"), exist_ok=True)
    # trace waterfall: search the Traces source for one request and open its root span
    try:
        import urllib.parse
        tid = json.load(open(os.path.join(EVID, "payloads", "04-stored-rows.json")))["trace_id"]
        now = int(time.time() * 1000)
        q = urllib.parse.urlencode({"source": st["sources"]["traces"], "where": f'TraceId:"{tid}"', "whereLanguage": "lucene",
                                    "from": now - 6 * 3600 * 1000, "to": now + 60000, "isLive": "false"})
        out = os.path.join(EVID, "screens", "trace-waterfall.png")
        r = sh("node", os.path.join(HERE, "shot.mjs"), f"http://localhost:8080/search?{q}", out, "--cookie", cookie, "--wait", "7000",
               "--width", "1500", "--height", "900", "--click", "GET /chain", timeout=120)
        note("screens/trace-waterfall.png", "HyperDX /search (Traces source, TraceId filter, span clicked)", "node course/tools/shot.mjs ... --click 'GET /chain'",
             ok=os.path.exists(out), output=r.stdout.strip()[-120:])
    except FileNotFoundError:
        pass  # needs evidence/payloads from capture-payloads.py
    try:
        tid = json.load(open(os.path.join(EVID, "checkout-trace-sample.json")))["trace_id"]
        now = int(time.time() * 1000)
        q = urllib.parse.urlencode({"source": st["sources"]["traces"], "where": f'TraceId:"{tid}"', "whereLanguage": "lucene",
                                    "from": now - 6 * 3600 * 1000, "to": now + 60000, "isLive": "false"})
        out = os.path.join(EVID, "screens", "trace-waterfall-checkout.png")
        r = sh("node", os.path.join(HERE, "shot.mjs"), f"http://localhost:8080/search?{q}", out, "--cookie", cookie, "--wait", "7000",
               "--width", "1500", "--height", "1300", "--click", "GET /checkout", timeout=120)
        note("screens/trace-waterfall-checkout.png", "HyperDX /search (Traces source, TraceId filter, /checkout root span clicked)",
             "node course/tools/shot.mjs ... --click 'GET /checkout'", ok=os.path.exists(out), output=r.stdout.strip()[-120:])
    except FileNotFoundError:
        pass  # needs evidence/checkout-trace-sample.json from the samples step
    for fname, path, w, h in shots:
        out = os.path.join(EVID, "screens", fname)
        r = sh("node", os.path.join(HERE, "shot.mjs"), f"http://localhost:8080{path}", out, "--cookie", cookie, "--wait", "9000",
               "--width", str(w), "--height", str(h), timeout=120)
        note(f"screens/{fname}", f"HyperDX {path}", "node course/tools/shot.mjs <url> <png> (headless Chrome, DevTools Protocol)",
             ok=os.path.exists(out), output=r.stdout.strip()[-120:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screens", action="store_true")
    ap.add_argument("--war-stories", action="store_true")
    ap.add_argument("--chaos-replays", action="store_true")
    args = ap.parse_args()
    os.makedirs(EVID, exist_ok=True)
    reports()
    samples()
    misc()
    if args.war_stories:
        war_stories()
    if args.chaos_replays:
        chaos_replays()
    if args.screens:
        screens()
    old = os.path.join(EVID, "manifest.json")
    prev = json.load(open(old)) if os.path.exists(old) else {}
    prev.update(MANIFEST)
    write("manifest.json", prev)
    print(f"evidence updated: {len(MANIFEST)} items this run, {len(prev)} in the manifest -> {EVID}")


if __name__ == "__main__":
    main()
