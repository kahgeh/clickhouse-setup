#!/usr/bin/env python3
"""Reliability / chaos tests: inject failures WHILE load is flowing, then require that nothing was
lost or duplicated, using the same ground-truth checks as verify.py.

  chaos.py [scenario ...]     scenarios: ingestor-crash, ingestor-restart, clickhouse-outage,
                              s3-blip, s3-outage-long, bad-payload, collector-restart-idle, dedup-token
                              (default: all)

Failures hit the ingestion side (Buffer consumer, ClickHouse, object store). The app->collector
edge is at-least-once by design (an SDK retry after a lost ack can duplicate a request's telemetry),
so the collector is restarted only while idle, and then must recover for the next load.
Exit code is non-zero if any scenario fails.
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify  # noqa: E402

COUNTS = "ok=120,slow=40,error=30,exception=10,chain=90,chain-fail=20"
PROJECT = "obs"
CTR = lambda svc: f"{PROJECT}-{svc}-1"


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
    return r


def start_load(run_id, counts=COUNTS, concurrency=1, slow_ms=200):
    outdir = os.path.join(HERE, "out", run_id)
    os.makedirs(outdir, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--network", verify.NETWORK, "-v", f"{outdir}:/out", verify.APP_IMAGE,
           "loadgen", "--base-url", verify.WEB_URL_IN_NET, "--run-id", run_id, "--counts", counts,
           "--slow-ms", str(slow_ms), "--concurrency", str(concurrency), "--out", "/out/ground_truth.json"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def settle(run_id, exp_spans, exp_logs, timeout=150):
    """Wait until the run's rows stop changing and reach the expected totals."""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            s = int(verify.q1(f"SELECT count() c FROM otel.otel_traces WHERE SpanAttributes['test_run_id']={verify.lit(run_id)}")["c"])
            l = int(verify.q1(f"SELECT count() c FROM otel.otel_logs WHERE LogAttributes['test_run_id']={verify.lit(run_id)}")["c"])
            last = (s, l)
            if last == (exp_spans, exp_logs):
                return time.time() - t0, last
        except Exception as e:  # ClickHouse may still be coming back
            last = f"query failed: {str(e)[:80]}"
        time.sleep(2)
    return None, last


def run_verify(run_id):
    time.sleep(8)  # let the last cumulative metric snapshots land
    cmd = [sys.executable, os.path.join(HERE, "verify.py"), "--run-id", run_id, "--reuse",
           "--skip-hyperdx", "--counts", COUNTS]
    stale = os.path.join(HERE, "out", run_id, "report.json")
    if os.path.exists(stale):
        os.remove(stale)
    subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(stale):
        return None
    with open(stale) as f:
        rep = json.load(f)
    return sorted({c["id"] for c in rep["checks"] if not c["ok"]}), rep["passed"], len(rep["checks"])


def dedup_drops():
    """Replayed inserts ClickHouse has dropped as duplicates so far. Read from the server log file
    (docker logs only carries the entrypoint output); the scratch probe table is excluded."""
    r = sh("docker", "exec", CTR("clickhouse"), "sh", "-c",
           "grep 'already exists as part' /var/log/clickhouse-server/clickhouse-server.log | grep -vc chaos_dedup_probe",
           check=False)
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return 0


def scenario_with_fault(name, fault):
    run_id = f"chaos-{name}-{time.strftime('%H%M%S')}"
    print(f"\n=== {name}  (run {run_id})")
    drops_before = dedup_drops()
    load = start_load(run_id)
    time.sleep(4)  # load is mid-flight
    print("   injecting fault ...")
    fault()
    out, _ = load.communicate(timeout=300)
    if load.returncode != 0:
        print("   loadgen failed:", out[-300:])
        return False
    with open(os.path.join(HERE, "out", run_id, "ground_truth.json")) as f:
        gt = json.load(f)
    delay, last = settle(run_id, gt["totals"]["expected_spans"], gt["totals"]["expected_logs"])
    print(f"   settled after {delay:.0f}s" if delay is not None else f"   DID NOT SETTLE: saw {last}")
    res = run_verify(run_id)
    if res is None:
        print("   verify crashed")
        return False
    failed, passed, total = res
    # E16 does not apply in --reuse mode; everything else must pass
    ok = delay is not None and not failed
    print(f"   {'PASS' if ok else 'FAIL'}: {passed}/{total} checks; failed={failed}")
    print(f"   info: replayed inserts dropped as duplicates by ClickHouse during this scenario: {dedup_drops() - drops_before}")
    return ok


# ------------------------------------------------------------------ faults


def fault_ingestor_crash():
    for svc in ("ingestor-traces", "ingestor-logs", "ingestor-metrics"):
        sh("docker", "kill", CTR(svc))  # SIGKILL: no graceful ack flush
    time.sleep(3)  # restart policy brings them back; also nudge in case the policy is slow
    for svc in ("ingestor-traces", "ingestor-logs", "ingestor-metrics"):
        sh("docker", "start", CTR(svc), check=False)


def fault_ingestor_restart():
    for svc in ("ingestor-traces", "ingestor-logs", "ingestor-metrics"):
        sh("docker", "restart", "-t", "2", CTR(svc))
        time.sleep(2)


def fault_clickhouse_outage():
    sh("docker", "stop", "-t", "5", CTR("clickhouse"))
    time.sleep(20)  # longer than the writer's retry budget
    sh("docker", "start", CTR("clickhouse"))


def fault_s3_blip(seconds=8):
    sh("docker", "stop", "-t", "3", CTR("s3"))
    time.sleep(seconds)
    sh("docker", "start", CTR("s3"))


# ------------------------------------------------------------------ non-load scenarios


def scenario_collector_restart_idle():
    name = "collector-restart-idle"
    print(f"\n=== {name}")
    sh("docker", "restart", "-t", "5", CTR("otel-collector"))
    time.sleep(6)
    # the app's OTLP exporters must reconnect on their own; a full verification run proves it
    rc = subprocess.run([sys.executable, os.path.join(HERE, "verify.py"), "--skip-hyperdx",
                         "--counts", "ok=10,slow=2,error=2,exception=1,chain=5,chain-fail=2",
                         "--run-id", f"chaos-{name}-{time.strftime('%H%M%S')}"],
                        capture_output=True, text=True)
    tail = [l for l in rc.stdout.splitlines() if "checks passed" in l or "[FAIL" in l]
    print("   " + "; ".join(tail))
    return rc.returncode == 0


def scenario_dedup_token():
    """Insert-time dedup: the same chunk replayed with the same token (what a retry after a crash
    mid-commit produces) must not create a second copy; a different token must."""
    name = "dedup-token"
    print(f"\n=== {name}")
    table = "otel.chaos_dedup_probe"
    verify.ch(f"DROP TABLE IF EXISTS {table}", fmt="TabSeparated")
    verify.ch(f"""CREATE TABLE {table} (k UInt64, _adapter_version UInt32) ENGINE = ReplacingMergeTree(_adapter_version)
                  ORDER BY k SETTINGS non_replicated_deduplication_window = 1000""", fmt="TabSeparated")

    def insert(token):
        import urllib.parse
        import urllib.request
        url = verify.CH_URL + "?" + urllib.parse.urlencode(
            {"query": f"INSERT INTO {table} FORMAT JSONEachRow", "insert_deduplication_token": token})
        req = urllib.request.Request(url, data=b'{"k":1,"_adapter_version":1}\n{"k":2,"_adapter_version":1}\n',
                                     headers={"Authorization": verify.CH_AUTH}, method="POST")
        urllib.request.urlopen(req, timeout=30).read()

    insert("tok-A")
    insert("tok-A")  # replay of the same chunk
    after_replay = int(verify.q1(f"SELECT count() c FROM {table}")["c"])
    insert("tok-B")  # genuinely new chunk with identical contents (e.g. different token) is kept
    after_new = int(verify.q1(f"SELECT count() c FROM {table}")["c"])
    final = int(verify.q1(f"SELECT count() c FROM {table} FINAL")["c"])
    verify.ch(f"DROP TABLE {table}", fmt="TabSeparated")
    ok = (after_replay, after_new, final) == (2, 4, 2)
    print(f"   rows after replay={after_replay} (want 2), after new token={after_new} (want 4), FINAL={final} (want 2)")
    print(f"   {'PASS' if ok else 'FAIL'}")
    return ok


def ensure_injector():
    out = os.path.join(HERE, "bin")
    os.makedirs(out, exist_ok=True)
    if not os.path.exists(os.path.join(out, "inject")):
        collector = os.path.join(os.path.dirname(os.path.dirname(HERE)), "collector")
        sh("docker", "run", "--rm", "-v", f"{collector}:/src", "-v", f"{out}:/out",
           "-w", "/src/exporter/opendataexporter", "docker.io/library/golang:1.26",
           "sh", "-c", "go build -o /out/inject ./cmd/inject")
    return out


def inject_garbage(per_signal=3):
    out = ensure_injector()
    for sig in ("logs", "traces", "metrics"):
        r = sh("docker", "run", "--rm", "--network", verify.NETWORK, "-v", f"{out}:/b:ro",
               "-e", "AWS_ACCESS_KEY_ID=localadmin", "-e", "AWS_SECRET_ACCESS_KEY=localadmin-secret",
               "--entrypoint", "/b/inject", verify.APP_IMAGE,
               "--bucket", "observability-buffer", "--region", "us-east-1", "--endpoint", "http://s3:9000",
               "--manifest", f"otel/{sig}/manifest", "--prefix", f"otel/{sig}/data", "--signal", sig,
               "--count", str(per_signal))
        print("   " + r.stdout.strip())


def malformed_counts():
    """Cumulative per-signal count of skipped entries, as each ingestor reports it on /metrics."""
    import re
    import urllib.request
    out = {}
    for port, sig in ((9091, "logs"), (9092, "traces"), (9093, "metrics")):
        text = urllib.request.urlopen(f"http://localhost:{port}/metrics", timeout=10).read().decode()
        m = re.search(r'ingestor_decode_malformed_entries_total\{signal="%s"\} (\d+)' % sig, text)
        out[sig] = int(m.group(1)) if m else 0
    return out


def scenario_bad_payload():
    name = "bad-payload"
    before = malformed_counts()  # counters are cumulative for the process lifetime: compare deltas
    ok = scenario_with_fault(name, inject_garbage)
    after = malformed_counts()
    delta = {k: after[k] - before[k] for k in after}
    up = all("Up" in sh("docker", "ps", "--filter", f"name={CTR('ingestor-' + s)}", "--format", "{{.Status}}").stdout
             for s in ("logs", "traces", "metrics"))
    counted = all(v == 3 for v in delta.values())
    print(f"   ingestors up={up}; malformed entries counted this run={delta} (want 3 each)")
    return ok and up and counted


SCENARIOS = {
    "bad-payload": scenario_bad_payload,
    "ingestor-crash": lambda: scenario_with_fault("ingestor-crash", fault_ingestor_crash),
    "ingestor-restart": lambda: scenario_with_fault("ingestor-restart", fault_ingestor_restart),
    "clickhouse-outage": lambda: scenario_with_fault("clickhouse-outage", fault_clickhouse_outage),
    "s3-blip": lambda: scenario_with_fault("s3-blip", fault_s3_blip),
    "s3-outage-long": lambda: scenario_with_fault("s3-outage-long", lambda: fault_s3_blip(30)),
    "collector-restart-idle": scenario_collector_restart_idle,
    "dedup-token": scenario_dedup_token,
}


def main():
    names = sys.argv[1:] or list(SCENARIOS)
    results = {}
    for n in names:
        try:
            results[n] = SCENARIOS[n]()
        except Exception as e:  # a harness error is a failure, never a silent skip
            print(f"   ERROR in {n}: {e}")
            results[n] = False
    print("\n== chaos summary")
    for n, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {n}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
