#!/usr/bin/env python3
"""Capture the real payload at each hop for ONE /chain request into course/evidence/payloads/ (standard library only).

  capture-payloads.py

Hops captured:
  01  app -> collector     OTLP as decoded by a throwaway debug collector on a separate network (no change to the stack)
  02  collector -> Buffer  the queued manifest entry, envelope bytes and OTLP payload decoded from the S3 batch object
                           (the traces ingestor is paused for a few seconds so the entry is still queued, then restarted)
  03  ingestor -> ClickHouse  the INSERT statement and its idempotency token, from ClickHouse system.query_log
  04  stored row           the same span as stored, as JSONEachRow (with the provenance columns)
  05  HyperDX -> ClickHouse the SQL HyperDX sent for the dashboard, from system.query_log (user hyperdx_ro)
  06  HyperDX -> webhook   the alert notifications (from evidence/alert-deliveries.json)
Requires the stack running, course/tools/bin/peek (built from collector/exporter/opendataexporter/cmd/peek), Node and Chrome.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
COURSE = os.path.dirname(HERE)
ROOT = os.path.dirname(COURSE)
OUT = os.path.join(COURSE, "evidence", "payloads")
BIN = os.path.join(HERE, "bin")
CH = "http://localhost:8123/"
CH_AUTH = "Basic " + base64.b64encode(b"ingestor:ingestor").decode()
NET = "payload-net"


def sh(*cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ch(sql):
    req = urllib.request.Request(CH + "?default_format=JSONEachRow", data=sql.encode(), headers={"Authorization": CH_AUTH}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return [json.loads(x) for x in r.read().decode().splitlines() if x.strip()]


def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, indent=2, default=str))


def chain_request(base_url, network, run_id):
    """Send exactly one /chain request with the load generator and return its trace id."""
    od = os.path.join(OUT, "_gt")
    os.makedirs(od, exist_ok=True)
    r = sh("docker", "run", "--rm", "--network", network, "-v", f"{od}:/out", "localhost/sre-app:dev", "loadgen", "--base-url", base_url,
           "--run-id", run_id, "--counts", "chain=1", "--concurrency", "1", "--out", "/out/gt.json")
    gt = json.load(open(os.path.join(od, "gt.json")))
    shutil.rmtree(od, ignore_errors=True)
    return gt["trace_ids"]["chain"][0]


def hop1_otlp():
    """Throwaway debug collector + throwaway app instances on their own network: nothing in the real stack changes."""
    sh("docker", "network", "rm", NET)
    sh("docker", "network", "create", NET)
    cfg = os.path.join(OUT, "_debug.yaml")
    os.makedirs(OUT, exist_ok=True)
    open(cfg, "w").write("""receivers:
  otlp: {protocols: {grpc: {endpoint: 0.0.0.0:4317}}}
exporters:
  debug: {verbosity: detailed}
service:
  pipelines:
    traces: {receivers: [otlp], exporters: [debug]}
    logs: {receivers: [otlp], exporters: [debug]}
""")
    names = ["pl-collector", "pl-backend", "pl-web"]
    for n in names:
        sh("docker", "rm", "-f", n)
    sh("docker", "run", "-d", "--name", "pl-collector", "--network", NET, "--network-alias", "pl-collector", "-v", f"{cfg}:/etc/otelcol/config.yaml:ro",
       "localhost/sre-collector:dev", "--config=/etc/otelcol/config.yaml")
    sh("docker", "run", "-d", "--name", "pl-backend", "--network", NET, "--network-alias", "pl-backend", "-e", "OTEL_EXPORTER_OTLP_ENDPOINT=http://pl-collector:4317",
       "-e", "SERVICE_NAME=backend", "localhost/sre-app:dev", "backend")
    sh("docker", "run", "-d", "--name", "pl-web", "--network", NET, "--network-alias", "pl-web", "-e", "OTEL_EXPORTER_OTLP_ENDPOINT=http://pl-collector:4317",
       "-e", "SERVICE_NAME=web", "-e", "BACKEND_URL=http://pl-backend:50051", "localhost/sre-app:dev", "web")
    time.sleep(6)
    tid = chain_request("http://pl-web:8081", NET, "payload-otlp")
    time.sleep(8)  # batch export interval
    log = sh("docker", "logs", "pl-collector")
    text = log.stdout + log.stderr
    # keep the detailed span and log dumps that belong to this trace
    blocks = re.split(r"(?=^\S+\s+info\s+(?:Traces|Logs)\b)", text, flags=re.M)
    keep = [b for b in blocks if tid in b]
    save("01-otlp-decoded-by-debug-collector.txt",
         f"# One GET /chain request. Trace id {tid}\n# Decoded by a throwaway collector's debug exporter (detailed). The wire format is protobuf over gRPC.\n\n" + "\n".join(b.rstrip() for b in keep))
    for n in names:
        sh("docker", "rm", "-f", n)
    sh("docker", "network", "rm", NET)
    os.remove(cfg)
    return tid


def hop2_buffer():
    sh("docker", "stop", "-t", "5", "obs-ingestor-traces-1")
    try:
        tid = chain_request("http://web:8081", "obs_default", "payload-buffer")
        time.sleep(4)
        found = None
        out = sh("docker", "run", "--rm", "--network", "obs_default", "-v", f"{BIN}:/b:ro", "-e", "AWS_ACCESS_KEY_ID=localadmin",
                 "-e", "AWS_SECRET_ACCESS_KEY=localadmin-secret", "--entrypoint", "/b/peek", "localhost/sre-app:dev",
                 "--bucket", "observability-buffer", "--region", "us-east-1", "--endpoint", "http://s3:9000",
                 "--manifest", "otel/traces/manifest", "--limit", "40")
        if out.returncode:
            raise RuntimeError(out.stderr)
        doc = json.loads(out.stdout)
        for e in doc["entries"]:
            if tid in json.dumps(e.get("otlp_payload_decoded", {})):
                found = e
        if not found:
            raise RuntimeError(f"trace {tid} not found in {doc['queued_entries']} queued entries")
        doc["entries"] = [found]
        doc["note"] = f"Manifest entry containing trace {tid}; the ingestor for traces was paused so it was still queued"
        save("02-buffer-manifest-and-batch.json", doc)
    finally:
        sh("docker", "start", "obs-ingestor-traces-1")
    for _ in range(30):
        if int(ch(f"SELECT count() n FROM otel.otel_traces WHERE TraceId='{tid}'")[0]["n"]) >= 4:
            break
        time.sleep(2)
    return tid


def hop3_4(tid):
    time.sleep(2)
    ch("SYSTEM FLUSH LOGS")
    q = ch("""SELECT event_time, user, query, written_rows, Settings['insert_deduplication_token'] AS dedup_token, Settings['async_insert'] AS async_insert
              FROM system.query_log WHERE type='QueryFinish' AND query LIKE 'INSERT INTO otel.otel_traces%' AND written_rows>0
              ORDER BY event_time DESC LIMIT 1""")
    save("03-clickhouse-insert.json", {"note": "The statement the ingestor sent (the rows travel in the HTTP body as JSONEachRow, one JSON object per line) and the settings it carried",
                                       **(q[0] if q else {})})
    rows = ch(f"SELECT * FROM otel.otel_traces WHERE TraceId='{tid}' ORDER BY Timestamp")
    save("04-stored-rows.json", {"note": "The four spans of the request as stored in ClickHouse (JSONEachRow form, provenance columns last)", "trace_id": tid, "rows": rows})
    logs = ch(f"SELECT * FROM otel.otel_logs WHERE TraceId='{tid}' ORDER BY Timestamp")
    save("04-stored-logs.json", {"trace_id": tid, "rows": logs})


def hop5():
    sys.path.insert(0, os.path.join(ROOT, "stack", "hyperdx"))
    import provision  # noqa: E402
    s = provision.Session()
    s.call("POST", f"{provision.UI}/api/login/password", {"email": provision.EMAIL, "password": provision.PASSWORD})
    st = json.load(open(os.path.join(ROOT, "stack", "hyperdx", "state.json")))
    cookie = "connect.sid=" + s.cookies["connect.sid"]
    sh("node", os.path.join(HERE, "shot.mjs"), f"http://localhost:8080/dashboards/{st['dashboards']['Availability & Latency']}",
       os.path.join(OUT, "_tmp.png"), "--cookie", cookie, "--wait", "9000", "--width", "1500", "--height", "1100", timeout=120)
    os.remove(os.path.join(OUT, "_tmp.png"))
    time.sleep(2)
    ch("SYSTEM FLUSH LOGS")
    qs = ch("""SELECT event_time, user, query_duration_ms, read_rows, read_bytes, query FROM system.query_log
               WHERE type='QueryFinish' AND user='hyperdx_ro' AND query LIKE '%otel_traces%' AND event_time > now() - INTERVAL 5 MINUTE
               ORDER BY event_time DESC LIMIT 40""")
    pick = {}
    for q in qs:
        text = q["query"]
        kind = "latency percentile tile" if "quantile" in text.lower() else ("grouped count tile" if "GROUP BY" in text else "other")
        pick.setdefault(kind, q)
    lines = ["# SQL that HyperDX sent to ClickHouse while rendering the Availability & Latency dashboard, read from system.query_log (user hyperdx_ro).\n"]
    for kind, q in pick.items():
        if kind == "other":
            continue
        lines.append(f"## {kind}: {q['query_duration_ms']} ms, read {q['read_rows']} rows, {q['read_bytes']} bytes\n{q['query'].strip()}\n")
    save("05-hyperdx-queries.txt", "\n".join(lines))


def main():
    os.makedirs(OUT, exist_ok=True)
    t1 = hop1_otlp()
    print("hop 1 captured, trace", t1)
    t2 = hop2_buffer()
    print("hop 2 captured, trace", t2)
    hop3_4(t2)
    print("hop 3-4 captured")
    hop5()
    print("hop 5 captured")
    src = os.path.join(COURSE, "evidence", "alert-deliveries.json")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(OUT, "06-alert-webhook.json"))
    man_path = os.path.join(COURSE, "evidence", "manifest.json")
    man = json.load(open(man_path))
    for f in sorted(os.listdir(OUT)):
        man[f"payloads/{f}"] = {"source": "course/tools/capture-payloads.py", "command": "python3 course/tools/capture-payloads.py",
                                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    json.dump(man, open(man_path, "w"), indent=2)
    print("payloads written to", OUT)


if __name__ == "__main__":
    main()
