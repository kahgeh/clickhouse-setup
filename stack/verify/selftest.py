#!/usr/bin/env python3
"""Negative tests for the verification suite: tamper with ClickHouse data after a clean run and
assert the expected checks FAIL. A verifier that cannot fail proves nothing.

  selftest.py        # ~2-3 minutes; exits non-zero if any mutation goes undetected
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify  # noqa: E402

import time
TAG = time.strftime("%H%M%S")  # unique per invocation: run ids must never be reused
MIX = "ok=6,slow=1,error=2,exception=1,chain=4,chain-fail=2,checkout=2"


def run_verify(run_id, reuse=False):
    cmd = [sys.executable, os.path.join(HERE, "verify.py"), "--run-id", run_id, "--counts", MIX, "--slow-ms", "200"]
    if reuse:
        cmd.append("--reuse")
    stale = os.path.join(HERE, "out", run_id, "report.json")
    if os.path.exists(stale):
        os.remove(stale)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(stale):
        raise SystemExit(f"verify.py crashed for {run_id}:\n{r.stdout[-800:]}\n{r.stderr[-1500:]}")
    with open(os.path.join(HERE, "out", run_id, "report.json")) as f:
        rep = json.load(f)
    failed = sorted({c["id"] for c in rep["checks"] if not c["ok"]})
    return r.returncode, failed


def mutate(sql):
    verify.ch(sql, fmt="TabSeparated")


def one_span(run_id, where):
    row = verify.q1(f"SELECT SpanId i, TraceId t FROM otel.otel_traces WHERE SpanAttributes['test_run_id']={verify.lit(run_id)} AND {where} LIMIT 1")
    return row["i"], row["t"]


def one_log_key(run_id):
    r = verify.q1(f"SELECT _odb_sequence s, _odb_entry_index e, _odb_record_index i FROM otel.otel_logs WHERE LogAttributes['test_run_id']={verify.lit(run_id)} LIMIT 1")
    return f"_odb_sequence = {r['s']} AND _odb_entry_index = {r['e']} AND _odb_record_index = {r['i']}"


def reparent_to_root(run_id, name):
    """Move one function span of a /checkout trace directly under the trace's root span."""
    span_id, trace_id = one_span(run_id, f"SpanName={verify.lit(name)} AND ServiceName='backend'")
    root = verify.q1(f"SELECT SpanId i FROM otel.otel_traces WHERE TraceId={verify.lit(trace_id)} AND ParentSpanId=''")["i"]
    mutate(f"ALTER TABLE otel.otel_traces UPDATE ParentSpanId = {verify.lit(root)} WHERE SpanId = {verify.lit(span_id)} SETTINGS mutations_sync = 2")


MUTATIONS = [
    ("delete one /ok span",
     lambda r: mutate(f"ALTER TABLE otel.otel_traces DELETE WHERE SpanId = {verify.lit(one_span(r, "SpanName='GET /ok'")[0])} SETTINGS mutations_sync = 2"),
     {"A2", "D12", "D13a"}),
    ("flip one /ok span to Error",
     lambda r: mutate(f"ALTER TABLE otel.otel_traces UPDATE StatusCode = 'Error' WHERE SpanId = {verify.lit(one_span(r, "SpanName='GET /ok'")[0])} SETTINGS mutations_sync = 2"),
     {"A2", "D13b", "D14a"}),
    ("duplicate one span row (new provenance-independent copy)",
     lambda r: mutate(f"INSERT INTO otel.otel_traces SELECT * FROM otel.otel_traces WHERE SpanId = {verify.lit(one_span(r, "SpanName='GET /ok'")[0])} LIMIT 1 SETTINGS insert_deduplicate = 0"),
     {"A4t"}),
    ("detach a backend span from its parent",
     lambda r: mutate(f"ALTER TABLE otel.otel_traces UPDATE ParentSpanId = 'deadbeefdeadbeef' WHERE SpanId = {verify.lit(one_span(r, "ServiceName='backend' AND SpanKind='Server' AND StatusCode='Unset' AND SpanName='backend.Process'")[0])} SETTINGS mutations_sync = 2"),
     {"B5"}),
    ("move one /checkout function span under the wrong parent",
     lambda r: reparent_to_root(r, "read_shelf"),
     {"B9"}),
    ("delete one log record",
     lambda r: mutate(f"ALTER TABLE otel.otel_logs DELETE WHERE LogAttributes['test_run_id']={verify.lit(r)} AND {one_log_key(r)} SETTINGS mutations_sync = 2"),
     {"A1", "D12"}),
    # The app re-exports cumulative snapshots every 5s, so deleted rows would be replaced; corrupt values instead.
    ("inflate web app.requests{/ok} counter values",
     lambda r: mutate(f"ALTER TABLE otel.otel_metrics_sum UPDATE Value = Value + 100 WHERE ServiceName='web' AND MetricName='app.requests' AND Attributes['test_run_id']={verify.lit(r)} AND Attributes['route']='/ok' SETTINGS mutations_sync = 2"),
     {"A3a"}),
    ("corrupt the /slow latency histogram count",
     lambda r: mutate(f"ALTER TABLE otel.otel_metrics_histogram UPDATE Count = Count + 7 WHERE ServiceName='web' AND MetricName='http.server.request.duration' AND Attributes['test_run_id']={verify.lit(r)} AND Attributes['http.route']='/slow' SETTINGS mutations_sync = 2"),
     {"A3c"}),
]


def main():
    print("== baseline: untouched run must pass")
    rid = f"selftest-{TAG}-base"
    verify_args = None
    rc, failed = run_verify(rid)
    ok_all = True
    print(f"   baseline rc={rc} failed={failed}")
    ok_all &= rc == 0 and not failed
    for i, (name, apply, expect) in enumerate(MUTATIONS):
        rid = f"selftest-{TAG}-m{i}"
        rc, failed = run_verify(rid)  # fresh clean run
        if rc != 0:
            print(f"!! {name}: clean run before mutation already failed: {failed}")
            ok_all = False
            continue
        apply(rid)
        rc, failed = run_verify(rid, reuse=True)
        detected = rc != 0 and set(expect) <= set(failed)
        print(f"{'DETECTED' if detected else 'MISSED  '} {name}: expected to fail {sorted(expect)}, failed {failed}")
        ok_all &= detected
    print("\nSELFTEST", "PASSED: every tampering was detected" if ok_all else "FAILED: some tampering went undetected")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
