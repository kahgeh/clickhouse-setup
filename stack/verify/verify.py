#!/usr/bin/env python3
"""End-to-end verification of the observability stack against load-generator ground truth.

  verify.py [--counts ok=100,slow=20,...] [--slow-ms 200] [--run-id ID] [--max-wait 90]

Runs the loadgen container (deterministic request mix + ground_truth.json), waits for the data
to land, then asserts, from three independent angles, that what arrived equals what was sent:
  * ClickHouse queries (the system of record)
  * the trace/log structure itself (parent/child links, correlation)
  * HyperDX's own APIs (search + chart series), i.e. what a user of the UI computes
Expected values are derived from the request mix and the telemetry contract, never from
the telemetry being verified. Exit code is non-zero if any check fails.
Standard library only. Dev-only credentials below are the compose defaults.
"""
import argparse
import base64
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
CH_URL = os.environ.get("CH_URL", "http://localhost:8123/")
CH_AUTH = "Basic " + base64.b64encode(
    f"{os.environ.get('CH_USER', 'ingestor')}:{os.environ.get('CH_PASSWORD', 'ingestor')}".encode()
).decode()
APP_IMAGE = os.environ.get("APP_IMAGE", "localhost/sre-app:dev")
NETWORK = os.environ.get("COMPOSE_NETWORK", "obs_default")
WEB_URL_IN_NET = os.environ.get("WEB_URL_IN_NET", "http://web:8081")
E2E_THRESHOLD_S = float(os.environ.get("E2E_THRESHOLD_S", "15"))
DEFAULT_COUNTS = "ok=100,slow=20,error=20,exception=5,chain=50,chain-fail=10,checkout=20"


# --------------------------------------------------------------------------- plumbing


def jsonable(v):
    """Make expected/actual values JSON-safe (tuple dict keys become 'a | b | c')."""
    if isinstance(v, dict):
        return {(" | ".join(map(str, k)) if isinstance(k, tuple) else str(k)): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    return v


class Report:
    def __init__(self):
        self.rows = []

    def check(self, cid, desc, expected, actual, ok=None):
        ok = (expected == actual) if ok is None else ok
        self.rows.append({"id": cid, "desc": desc, "expected": jsonable(expected),
                          "actual": jsonable(actual), "ok": bool(ok)})
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {cid:<5} {desc}" + ("" if ok else f"\n        expected={expected!r}\n        actual  ={actual!r}"))
        return ok

    def failed(self):
        return [r for r in self.rows if not r["ok"]]


def ch(sql, fmt="JSONEachRow"):
    req = urllib.request.Request(
        CH_URL + "?" + urllib.parse.urlencode({"default_format": fmt, "max_execution_time": 60}),
        data=sql.encode(), headers={"Authorization": CH_AUTH}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ClickHouse error {e.code}: {e.read().decode()[:500]}\nSQL: {sql}")
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def q1(sql):
    rows = ch(sql)
    return rows[0] if rows else {}


def lit(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


class HyperDX:
    def __init__(self, state):
        self.api, self.src, self.key = state["api"], state["sources"], state["accessKey"]

    def post(self, path, body):
        """POST to the HyperDX external API. It allows 100 requests per window and answers 429 with
        Retry-After beyond that, so wait and retry instead of failing the verification."""
        for attempt in range(6):
            req = urllib.request.Request(
                f"{self.api}/api/v2/{path}", data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 5:
                    wait = min(int(e.headers.get("Retry-After", "30")), 65) + 1
                    print(f"        (HyperDX rate limit hit; waiting {wait}s)")
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"HyperDX {path} -> {e.code}: {e.read().decode()[:500]}")

    def search(self, source, where, start, end, select="", limit=2000):
        body = {"sourceId": self.src[source], "where": where, "whereLanguage": "sql",
                "startTime": iso(start), "endTime": iso(end), "maxResults": limit}
        if select:
            body["select"] = select
        return self.post("search", body)["data"]

    def series(self, source, start, end, aggfn, where, field=None, level=None, group_by=None,
               granularity="1d", metric=None):
        s = {"sourceId": self.src[source], "aggFn": aggfn, "where": where, "whereLanguage": "sql",
             "groupBy": group_by or []}
        if metric:
            s["metricName"], s["metricDataType"] = metric
        if field:
            s["field"] = field
        if level is not None:
            s["level"] = level
        data = self.post("charts/series", {
            "startTime": int(start * 1000), "endTime": int(end * 1000), "granularity": granularity,
            "seriesReturnType": "column", "series": [s]})["data"]
        return data


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_counts(s):
    out = {}
    for part in s.split(","):
        k, v = part.split("=")
        out[k.strip()] = int(v)
    return out


# --------------------------------------------------------------------------- expectations
# Derived from the request mix and the telemetry contract (see app/README.md).


# The fictitious /checkout call tree (see app/web/src/checkout.rs and app/backend/src/checkout.rs); every function span comes from #[trace_all].
CHECKOUT_WEB_FUNCTIONS = ["place_order", "validate_cart", "load_cart", "price_items", "unit_price", "tax", "apply_discount", "reserve_inventory", "confirm_order"]
CHECKOUT_BACKEND_FUNCTIONS = ["lookup_stock", "read_shelf", "check_fraud", "load_history", "score", "authorise_payment", "call_acquirer", "write_ledger", "debit", "credit"]


# Expected parent of each /checkout span, keyed by (service, span name); the root has no parent.
CHECKOUT_PARENT = {
    ("web", "GET /checkout"): None, ("web", "place_order"): ("web", "GET /checkout"),
    ("web", "validate_cart"): ("web", "place_order"), ("web", "load_cart"): ("web", "validate_cart"),
    ("web", "price_items"): ("web", "place_order"), ("web", "unit_price"): ("web", "price_items"), ("web", "tax"): ("web", "price_items"),
    ("web", "apply_discount"): ("web", "place_order"), ("web", "reserve_inventory"): ("web", "place_order"),
    ("web", "backend.Checkout"): ("web", "reserve_inventory"), ("backend", "backend.Checkout"): ("web", "backend.Checkout"),
    ("backend", "lookup_stock"): ("backend", "backend.Checkout"), ("backend", "read_shelf"): ("backend", "lookup_stock"),
    ("backend", "check_fraud"): ("backend", "backend.Checkout"), ("backend", "load_history"): ("backend", "check_fraud"),
    ("backend", "score"): ("backend", "check_fraud"),
    ("backend", "authorise_payment"): ("backend", "backend.Checkout"), ("backend", "call_acquirer"): ("backend", "authorise_payment"),
    ("backend", "write_ledger"): ("backend", "backend.Checkout"), ("backend", "debit"): ("backend", "write_ledger"),
    ("backend", "credit"): ("backend", "write_ledger"),
    ("web", "confirm_order"): ("web", "place_order"),
}


def expected_spans(c):
    n = lambda k: c.get(k, 0)
    e = {}

    def add(service, kind, status, name, count):
        if count:
            e[(service, kind, status, name)] = e.get((service, kind, status, name), 0) + count

    add("web", "Server", "Unset", "GET /ok", n("ok"))
    add("web", "Server", "Unset", "GET /slow", n("slow"))
    add("web", "Server", "Error", "GET /error", n("error"))
    add("web", "Server", "Error", "GET /exception", n("exception"))
    add("web", "Server", "Unset", "GET /chain", n("chain"))
    add("web", "Client", "Unset", "backend.Process", n("chain"))
    add("backend", "Server", "Unset", "backend.Process", n("chain"))
    add("backend", "Internal", "Unset", "backend.compute", n("chain"))
    add("web", "Server", "Error", "GET /chain-fail", n("chain-fail"))
    add("web", "Client", "Error", "backend.Process", n("chain-fail"))
    add("backend", "Server", "Error", "backend.Process", n("chain-fail"))
    add("web", "Server", "Unset", "GET /checkout", n("checkout"))
    add("web", "Client", "Unset", "backend.Checkout", n("checkout"))
    add("backend", "Server", "Unset", "backend.Checkout", n("checkout"))
    for name in CHECKOUT_WEB_FUNCTIONS:
        add("web", "Internal", "Unset", name, n("checkout"))
    for name in CHECKOUT_BACKEND_FUNCTIONS:
        add("backend", "Internal", "Unset", name, n("checkout"))
    return e


def expected_logs(c):
    n = lambda k: c.get(k, 0)
    e = {
        ("web", "INFO"): n("ok") + n("slow") + n("chain") + n("checkout"),
        ("web", "ERROR"): n("error") + n("exception") + n("chain-fail"),
        ("backend", "INFO"): n("chain") + n("checkout"),
        ("backend", "ERROR"): n("chain-fail"),
    }
    return {k: v for k, v in e.items() if v}


def expected_route_outcomes(c):
    n = lambda k: c.get(k, 0)
    e = {("/ok", "ok"): n("ok"), ("/slow", "ok"): n("slow"), ("/chain", "ok"): n("chain"), ("/checkout", "ok"): n("checkout"),
         ("/error", "error"): n("error"), ("/exception", "error"): n("exception"),
         ("/chain-fail", "error"): n("chain-fail")}
    return {k: v for k, v in e.items() if v}


# --------------------------------------------------------------------------- run


def run_loadgen(run_id, counts, slow_ms, outdir):
    os.makedirs(outdir, exist_ok=True)
    cmd = ["docker", "run", "--rm", "--network", NETWORK, "-v", f"{outdir}:/out", APP_IMAGE,
           "loadgen", "--base-url", WEB_URL_IN_NET, "--run-id", run_id, "--counts", counts,
           "--slow-ms", str(slow_ms), "--concurrency", "4", "--out", "/out/ground_truth.json"]
    print("$", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        raise SystemExit(f"loadgen failed (rc={r.returncode}): its statuses differed from the contract")
    with open(os.path.join(outdir, "ground_truth.json")) as f:
        return json.load(f)


def wait_for_counts(run_id, exp_spans, exp_logs, max_wait):
    t0 = time.time()
    last = None
    while time.time() - t0 < max_wait:
        s = q1(f"SELECT count() c FROM otel.otel_traces WHERE SpanAttributes['test_run_id']={lit(run_id)}")
        l = q1(f"SELECT count() c FROM otel.otel_logs WHERE LogAttributes['test_run_id']={lit(run_id)}")
        last = (int(s["c"]), int(l["c"]))
        if last == (exp_spans, exp_logs):
            return time.time() - t0, last
        time.sleep(1)
    return None, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counts", default=DEFAULT_COUNTS)
    ap.add_argument("--slow-ms", type=int, default=200)
    ap.add_argument("--run-id", default="verify-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S"))
    ap.add_argument("--max-wait", type=int, default=90)
    ap.add_argument("--skip-hyperdx", action="store_true")
    ap.add_argument("--reuse", action="store_true",
                    help="re-verify an existing run (read out/<run-id>/ground_truth.json) without generating load")
    args = ap.parse_args()

    run_id, counts = args.run_id, parse_counts(args.counts)
    outdir = os.path.join(HERE, "out", run_id)
    rep = Report()
    print(f"== run {run_id}  mix {counts}")

    if args.reuse:
        with open(os.path.join(outdir, "ground_truth.json")) as f:
            gt = json.load(f)
        counts = {k: v["count"] for k, v in gt["per_endpoint"].items()}
    else:
        pre = int(q1(f"SELECT count() c FROM otel.otel_traces WHERE SpanAttributes['test_run_id']={lit(run_id)}")["c"])
        if pre:
            raise SystemExit(f"run id {run_id!r} already has {pre} spans: run ids must be unique "
                             "(re-using one would mix several runs and corrupt every count)")
        gt = run_loadgen(run_id, args.counts, args.slow_ms, outdir)
    t_end = time.time()
    t_start_s = gt["started_at_unix_nano"] / 1e9 - 5
    t_end_s = time.time() + 60

    # ---- ground truth self-consistency (loadgen math is independent of telemetry)
    exp_sp, exp_lg = expected_spans(counts), expected_logs(counts)
    total_sp, total_lg = sum(exp_sp.values()), sum(exp_lg.values())
    n_req = sum(counts.values())
    n_5xx = counts.get("error", 0) + counts.get("exception", 0) + counts.get("chain-fail", 0)
    rep.check("G1", "ground truth: requests / spans / logs / 5xx / availability match the contract",
              [n_req, total_sp, total_lg, n_5xx, round((n_req - n_5xx) / n_req, 9)],
              [gt["totals"]["requests"], gt["totals"]["expected_spans"], gt["totals"]["expected_logs"],
               gt["totals"]["expected_http_5xx"], round(gt["totals"]["expected_availability"], 9)])

    # ---- wait for data (also yields the end-to-end latency figure). A shortfall is reported but
    # does NOT stop the run: the remaining checks then pinpoint exactly what is wrong.
    delay = None
    if not args.reuse:
        delay, last = wait_for_counts(run_id, total_sp, total_lg, args.max_wait)
        if delay is None:
            rep.check("READY", f"all expected spans and logs became queryable within {args.max_wait}s",
                      [total_sp, total_lg], list(last) if last else None)
    if not args.reuse:
        time.sleep(7)  # metrics export interval is 5s; loadgen also force-flushed

    R = lit(run_id)
    # ---- A1 logs
    rows = ch(f"SELECT ServiceName s, SeverityText sev, count() c FROM otel.otel_logs WHERE LogAttributes['test_run_id']={R} GROUP BY 1,2")
    got = {(r["s"], r["sev"]): int(r["c"]) for r in rows}
    rep.check("A1", "logs: count per (service, severity) equals ground truth", exp_lg, got)

    # ---- A2 spans
    rows = ch(f"SELECT ServiceName s, SpanKind k, StatusCode st, SpanName n, count() c FROM otel.otel_traces WHERE SpanAttributes['test_run_id']={R} GROUP BY 1,2,3,4")
    got = {(r["s"], r["k"], r["st"], r["n"]): int(r["c"]) for r in rows}
    rep.check("A2", "spans: count per (service, kind, status, name) equals contract", exp_sp, got)

    # ---- A3 metrics (cumulative series: the last/max value per series is the run total)
    rows = ch(f"""SELECT Attributes['route'] r, Attributes['outcome'] o, max(Value) v FROM otel.otel_metrics_sum
                  WHERE ServiceName='web' AND MetricName='app.requests' AND Attributes['test_run_id']={R} GROUP BY 1,2""")
    got = {(r["r"], r["o"]): int(r["v"]) for r in rows}
    rep.check("A3a", "metrics: web app.requests per (route, outcome) equals request mix", expected_route_outcomes(counts), got)

    # cumulative counters: the latest value of each (route, outcome) series
    rows = ch(f"""SELECT Attributes['route'] r, Attributes['outcome'] o, max(Value) v FROM otel.otel_metrics_sum
                  WHERE ServiceName='backend' AND MetricName='app.requests' AND Attributes['test_run_id']={R} GROUP BY r, o""")
    exp_be = {k: v for k, v in {"Process | ok": counts.get("chain", 0), "Process | error": counts.get("chain-fail", 0),
                                "Checkout | ok": counts.get("checkout", 0)}.items() if v}
    rep.check("A3b", "metrics: backend app.requests per (rpc, outcome) equals chain / chain-fail / checkout counts",
              exp_be, {f"{r['r']} | {r['o']}": int(r["v"]) for r in rows})

    rows = ch(f"""SELECT Attributes['http.route'] r, max(Count) c FROM otel.otel_metrics_histogram
                  WHERE ServiceName='web' AND MetricName='http.server.request.duration' AND Attributes['test_run_id']={R} GROUP BY 1""")
    exp_h = {"/" + k: v for k, v in counts.items() if v}
    rep.check("A3c", "metrics: http.server.request.duration histogram count per route equals request mix",
              exp_h, {r["r"]: int(r["c"]) for r in rows})

    r = q1(f"""SELECT max(Count) c, max(Sum) s FROM otel.otel_metrics_histogram
               WHERE ServiceName='web' AND MetricName='http.server.request.duration'
                 AND Attributes['http.route']='/slow' AND Attributes['test_run_id']={R}""")
    if counts.get("slow"):
        lo = counts["slow"] * args.slow_ms / 1000.0
        rep.check("A3d", f"metrics: /slow histogram sum reflects the injected {args.slow_ms}ms delay",
                  f"{lo:.2f}s <= sum < {lo * 3:.2f}s", float(r.get("s", 0)), ok=lo <= float(r.get("s", 0)) < lo * 3)

    r = q1(f"""SELECT max(abs(v)) m FROM (SELECT argMax(Value, TimeUnix) v FROM otel.otel_metrics_sum
               WHERE MetricName='http.server.active_requests' AND Attributes['test_run_id']={R} GROUP BY Attributes)""")
    rep.check("A3e", "metrics: in-flight requests gauge settles at 0 on every route", 0, int(float(r.get("m", 0))))

    # ---- A4 dedup / idempotency of what we just ingested
    for table, key in (("otel_traces", "SpanAttributes"), ("otel_logs", "LogAttributes")):
        raw = int(q1(f"SELECT count() c FROM otel.{table} WHERE {key}['test_run_id']={R}")["c"])
        fin = int(q1(f"SELECT count() c FROM otel.{table} FINAL WHERE {key}['test_run_id']={R}")["c"])
        uniq = int(q1(f"SELECT uniqExact(_odb_sequence,_odb_entry_index,_odb_record_index) c FROM otel.{table} WHERE {key}['test_run_id']={R}")["c"])
        rep.check("A4t" if table == "otel_traces" else "A4l", f"{table}: no duplicate rows (raw == FINAL == unique provenance keys)", [raw, raw, raw], [raw, fin, uniq])

    for table in ("otel_metrics_sum", "otel_metrics_histogram"):
        # cumulative series keep exporting snapshots after the run, so pin all three counts to one queue position
        cut = int(q1(f"SELECT max(_odb_sequence) c FROM otel.{table}")["c"])
        scope = f"Attributes['test_run_id']={R} AND _odb_sequence <= {cut}"
        raw = int(q1(f"SELECT count() c FROM otel.{table} WHERE {scope}")["c"])
        fin = int(q1(f"SELECT count() c FROM otel.{table} FINAL WHERE {scope}")["c"])
        uniq = int(q1(f"SELECT uniqExact(_odb_sequence,_odb_entry_index,_odb_record_index) c FROM otel.{table} WHERE {scope}")["c"])
        rep.check("A4m", f"{table}: no duplicate metric points (raw == FINAL == unique provenance keys)", [raw, raw, raw], [raw, fin, uniq])

    # ---- E16 per-row end-to-end latency: server insert time minus when the event happened
    # (span end = start + duration; logs carry their own timestamp). Measured on every row.
    lat = ch(f"""SELECT 'spans' k, count() n, quantile(0.5)(l) p50, quantile(0.99)(l) p99, max(l) mx FROM (
                   SELECT (toUnixTimestamp64Nano(_odb_clickhouse_inserted_at) - toUnixTimestamp64Nano(Timestamp) - Duration) / 1e9 l
                   FROM otel.otel_traces WHERE SpanAttributes['test_run_id']={R})
                 UNION ALL
                 SELECT 'logs', count(), quantile(0.5)(l), quantile(0.99)(l), max(l) FROM (
                   SELECT (toUnixTimestamp64Nano(_odb_clickhouse_inserted_at) - toUnixTimestamp64Nano(Timestamp)) / 1e9 l
                   FROM otel.otel_logs WHERE LogAttributes['test_run_id']={R})""")
    figs = {r["k"]: {"rows": int(r["n"]), "p50_s": round(float(r["p50"]), 3), "p99_s": round(float(r["p99"]), 3),
                     "max_s": round(float(r["mx"]), 3)} for r in lat}
    if args.reuse:
        # Fault-injection runs are delayed on purpose, so the threshold only applies to clean runs.
        print(f"[INFO] E16   per-row latency (threshold not applied to --reuse/fault runs): {figs}")
    else:
        rep.check("E16", f"end-to-end latency (event -> queryable in ClickHouse), every row: p99 <= {E2E_THRESHOLD_S:.0f}s; "
                  f"measured {figs}", f"p99 <= {E2E_THRESHOLD_S}s", {k: v["p99_s"] for k, v in figs.items()},
                  ok=all(0 <= v["p99_s"] <= E2E_THRESHOLD_S for v in figs.values()))

    # ---- B distributed tracing
    check_tracing(rep, gt)

    if not args.skip_hyperdx:
        check_hyperdx(rep, gt, counts, run_id, t_start_s, t_end_s, args.slow_ms)
    return finish(rep, run_id, outdir, gt, delay)


def check_tracing(rep, gt):
    ids = gt.get("trace_ids", {})
    for endpoint, expect_spans, expect_err in (("chain", 4, False), ("chain-fail", 3, True)):
        tids = ids.get(endpoint, [])
        if not tids:
            continue
        inlist = ",".join(lit(t) for t in tids)
        spans = ch(f"SELECT TraceId t, ServiceName s, SpanKind k, StatusCode st, SpanName n, SpanId i, ParentSpanId p FROM otel.otel_traces WHERE TraceId IN ({inlist})")
        by = {}
        for s in spans:
            by.setdefault(s["t"], []).append(s)
        bad = []
        for t in tids:
            ss = by.get(t, [])
            index = {s["i"]: s for s in ss}
            roots = [s for s in ss if s["p"] == ""]
            ok = len(ss) == expect_spans and len(roots) == 1 and roots[0]["s"] == "web" and roots[0]["k"] == "Server"
            if ok:
                # expected chain web.server -> web.client -> backend.server [-> backend.internal]
                order = ["Server", "Client", "Server", "Internal"][:expect_spans]
                svc = ["web", "web", "backend", "backend"][:expect_spans]
                cur, path = roots[0], [roots[0]]
                while True:
                    kids = [s for s in ss if s["p"] == cur["i"]]
                    if len(kids) != 1:
                        break
                    cur = kids[0]
                    path.append(cur)
                ok = [p["k"] for p in path] == order and [p["s"] for p in path] == svc
                if ok and expect_err:
                    ok = all(p["st"] == "Error" for p in path)
                if ok and not expect_err:
                    ok = all(p["st"] == "Unset" for p in path)
                dangling = [s for s in ss if s["p"] != "" and s["p"] not in index]
                ok = ok and not dangling
            if not ok:
                bad.append(t)
        rep.check("B5" if endpoint == "chain" else "B7",
                  f"/{endpoint}: all {len(tids)} traces have the exact web->client->backend span chain"
                  + (" with Error propagated on every hop" if expect_err else " (no errors)"),
                  [], bad)
        # B6: logs carry the trace id from both services
        logs = ch(f"SELECT TraceId t, ServiceName s, SeverityText sev FROM otel.otel_logs WHERE TraceId IN ({inlist})")
        per = {}
        for l in logs:
            per.setdefault(l["t"], set()).add((l["s"], l["sev"]))
        want = {("web", "ERROR"), ("backend", "ERROR")} if expect_err else {("web", "INFO"), ("backend", "INFO")}
        badl = [t for t in tids if per.get(t) != want]
        rep.check("B6a" if endpoint == "chain" else "B6b", f"/{endpoint}: log correlation, each trace id has exactly the web+backend logs", [], badl)
    # B9: the /checkout trace has the whole fictitious call tree with every span under its expected parent
    tids = ids.get("checkout", [])
    if tids:
        inlist = ",".join(lit(t) for t in tids)
        spans = ch(f"SELECT TraceId t, ServiceName s, SpanKind k, StatusCode st, SpanName n, SpanId i, ParentSpanId p FROM otel.otel_traces WHERE TraceId IN ({inlist})")
        want = sorted([("web", "Server", "GET /checkout"), ("web", "Client", "backend.Checkout"), ("backend", "Server", "backend.Checkout")]
                      + [("web", "Internal", x) for x in CHECKOUT_WEB_FUNCTIONS] + [("backend", "Internal", x) for x in CHECKOUT_BACKEND_FUNCTIONS])
        by = {}
        for s in spans:
            by.setdefault(s["t"], []).append(s)
        bad = []
        for t in tids:
            ss = by.get(t, [])
            index = {s["i"]: s for s in ss}
            roots = [s for s in ss if s["p"] == ""]
            got = sorted((s["s"], s["k"], s["n"]) for s in ss)
            dangling = [s for s in ss if s["p"] != "" and s["p"] not in index]
            wrong_parent = [s for s in ss if s["p"] in index
                            and (index[s["p"]]["s"], index[s["p"]]["n"]) != CHECKOUT_PARENT.get((s["s"], s["n"]))]
            if got != want or len(roots) != 1 or roots[0]["n"] != "GET /checkout" or dangling or wrong_parent or any(s["st"] != "Unset" for s in ss):
                bad.append(t)
        rep.check("B9", f"/checkout: all {len(tids)} traces have exactly the {len(want)}-span call tree, each span under its expected parent, one root, no errors", [], bad)
    # root spans of every request carry the trace ids the client saw
    all_ids = [t for v in ids.values() for t in v]
    if all_ids:
        inlist = ",".join(lit(t) for t in all_ids)
        found = {r["t"] for r in ch(f"SELECT DISTINCT TraceId t FROM otel.otel_traces WHERE TraceId IN ({inlist})")}
        rep.check("B8", "every x-trace-id returned to the client exists in ClickHouse", 0, len(set(all_ids) - found))


def check_hyperdx(rep, gt, counts, run_id, start_s, end_s, slow_ms):
    state_path = os.path.join(STACK, "hyperdx", "state.json")
    if not os.path.exists(state_path):
        subprocess.run([sys.executable, os.path.join(STACK, "hyperdx", "provision.py")], check=True, capture_output=True)
    # also (re)create saved searches scoped to this run (runlog queries)
    subprocess.run([sys.executable, os.path.join(STACK, "hyperdx", "provision.py"), "runlog", run_id],
                   check=True, capture_output=True)
    hdx = HyperDX(json.load(open(state_path)))
    R = f"'{run_id}'"
    n_5xx = gt["totals"]["expected_http_5xx"]
    n_req = gt["totals"]["requests"]

    # D12: every source returns this run's data through HyperDX
    spans = hdx.search("traces", f"SpanAttributes['test_run_id'] = {R}", start_s, end_s)
    logs = hdx.search("logs", f"LogAttributes['test_run_id'] = {R}", start_s, end_s)
    rep.check("D12", "HyperDX search: Traces and Logs sources return exactly this run's spans and logs",
              [gt["totals"]["expected_spans"], gt["totals"]["expected_logs"]], [len(spans), len(logs)])

    # D12b: the Metrics source returns this run's metric data through HyperDX as well
    # HyperDX drops each cumulative series' first point, so data only becomes chartable after a
    # second export (every 5 s); poll instead of failing on a race right after the load ends.
    total_counter_points = 0.0
    for _ in range(6):
        mrows = hdx.series("metrics", start_s - 120, end_s, "count", "Attributes['test_run_id'] = " + R,
                           granularity="1m", metric=("app.requests", "sum"))
        total_counter_points = sum(float(r["series_0"]) for r in mrows)
        if total_counter_points > 0:
            break
        time.sleep(6)
    rep.check("D12b", "HyperDX Metrics source: the app.requests counter series has data points for this run",
              "> 0 data points", total_counter_points, ok=total_counter_points > 0)

    # D13: availability and latency as HyperDX computes them
    web = f"SpanKind='Server' AND ServiceName='web' AND SpanAttributes['test_run_id']={R}"
    total = sum(float(p["series_0"]) for p in hdx.series("traces", start_s, end_s, "count", web))
    errs = sum(float(p["series_0"]) for p in hdx.series("traces", start_s, end_s, "count", web + " AND StatusCode='Error'"))
    rep.check("D13a", "HyperDX chart: request count (web server spans) equals requests sent", n_req, int(total))
    avail = (total - errs) / total if total else 0
    rep.check("D13b", "HyperDX chart: availability (non-5xx / total) equals ground truth",
              round(gt["totals"]["expected_availability"], 6), round(avail, 6))
    if counts.get("slow"):
        p50 = [float(p["series_0"]) for p in hdx.series(
            "traces", start_s, end_s, "quantile", web + " AND SpanName='GET /slow'", field="Duration", level=0.5)]
        lo, hi = slow_ms * 1e6, slow_ms * 1e6 * 2
        rep.check("D13c", f"HyperDX chart: /slow p50 latency is consistent with the injected {slow_ms}ms",
                  f"{lo / 1e6:.0f}ms <= p50 < {hi / 1e6:.0f}ms", f"{p50[0] / 1e6:.1f}ms" if p50 else None,
                  ok=bool(p50) and lo <= p50[0] < hi)

    # D13d: per-route SLI as the dashboard's table tile computes it (groupBy through HyperDX)
    def per_route(extra):
        rows = hdx.series("traces", start_s, end_s, "count", web + extra, group_by=["SpanName"])
        return {r["group"][0] if isinstance(r.get("group"), list) else r.get("group", r.get("SpanName")):
                int(float(r["series_0"])) for r in rows}
    exp_routes = {"GET /" + k: v for k, v in counts.items() if v}
    rep.check("D13d", "HyperDX chart grouped by route: request counts per route equal the request mix",
              exp_routes, per_route(""))
    exp_err = {"GET /" + k: counts[k] for k in ("error", "exception", "chain-fail") if counts.get(k)}
    rep.check("D13e", "HyperDX chart grouped by route: error counts per route equal the 5xx mix",
              exp_err, per_route(" AND StatusCode='Error'"))

    # D16: the dashboards exist as code with the intended content
    dashes = {d["name"]: d for d in hdx_get(hdx, "dashboards")}
    av, rl = dashes.get("Availability & Latency"), dashes.get("Runlog")
    rep.check("D16", "dashboards exist: 'Availability & Latency' (13 tiles incl. availability, latency, error lists) and 'Runlog' (4 tiles, 2 filters)",
              {"availability_tiles": 13, "runlog_tiles": 4, "runlog_filters": 2},
              {"availability_tiles": len(av["tiles"]) if av else None, "runlog_tiles": len(rl["tiles"]) if rl else None,
               "runlog_filters": len(rl.get("filters", [])) if rl else None})

    # D14: error list = exactly the failures the generator caused
    failed = hdx.search("traces", f"{web} AND StatusCode = 'Error'", start_s, end_s,
                        select="Timestamp, ServiceName, SpanName, StatusMessage, TraceId")
    exp_names = {}
    for k in ("error", "exception", "chain-fail"):
        if counts.get(k):
            exp_names["GET /" + k] = counts[k]
    got_names = {}
    for r in failed:
        got_names[r["SpanName"]] = got_names.get(r["SpanName"], 0) + 1
    rep.check("D14a", "error list (failed web spans) = exactly the /error, /exception, /chain-fail requests", exp_names, got_names)
    err_logs = hdx.search("logs", f"LogAttributes['test_run_id'] = {R} AND SeverityText = 'ERROR'", start_s, end_s)
    rep.check("D14b", "error list (ERROR logs) equals expected error logs",
              sum(v for (s, sev), v in expected_logs(counts).items() if sev == "ERROR"), len(err_logs))

    # D14c: the saved *Error list* searches themselves (what a user opens), scoped to this run
    def saved_where(name):
        for attempt in range(6):
            req = urllib.request.Request(f"{hdx.api}/api/v2/saved-searches", headers={"Authorization": f"Bearer {hdx.key}"})
            try:
                return {x["name"]: x for x in json.load(urllib.request.urlopen(req))["data"]}[name]["where"]
            except urllib.error.HTTPError as e:
                if e.code != 429 or attempt == 5:
                    raise
                time.sleep(min(int(e.headers.get("Retry-After", "30")), 65) + 1)
    exp_err_spans = sum(v for (svc, kind, st, nm), v in expected_spans(counts).items() if st == "Error")
    sp = hdx.post("search", {"sourceId": hdx.src["traces"], "whereLanguage": "lucene", "maxResults": 2000,
                             "where": f'({saved_where("Error list: failed spans")}) AND SpanAttributes.test_run_id:"{run_id}"',
                             "startTime": iso(start_s), "endTime": iso(end_s)})["data"]
    lg = hdx.post("search", {"sourceId": hdx.src["logs"], "whereLanguage": "lucene", "maxResults": 2000,
                             "where": f'({saved_where("Error list: error logs")}) AND LogAttributes.test_run_id:"{run_id}"',
                             "startTime": iso(start_s), "endTime": iso(end_s)})["data"]
    rep.check("D14c", "saved searches 'Error list: failed spans' / 'error logs' return every failed span and ERROR log of the run",
              [exp_err_spans, sum(v for (sv, sev), v in expected_logs(counts).items() if sev == "ERROR")],
              [len(sp), len(lg)])

    # D15: runlog queries (saved searches created for this run) return the run's data
    for attempt in range(6):
        req = urllib.request.Request(f"{hdx.api}/api/v2/saved-searches", headers={"Authorization": f"Bearer {hdx.key}"})
        try:
            searches = {s["name"]: s for s in json.load(urllib.request.urlopen(req))["data"]}
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(min(int(e.headers.get("Retry-After", "30")), 65) + 1)
    ok_runlog = True
    detail = {}
    for kind, src, exp in (("logs", "logs", gt["totals"]["expected_logs"]), ("spans", "traces", gt["totals"]["expected_spans"])):
        s = searches.get(f"Runlog {run_id}: {kind}")
        if not s:
            ok_runlog, detail[kind] = False, "saved search missing"
            continue
        rows = hdx.search(src, s["where"], start_s, end_s)
        detail[kind] = len(rows)
        ok_runlog = ok_runlog and len(rows) == exp
    rep.check("D15", "runlog saved searches for this run return all of its logs and spans",
              {"logs": gt["totals"]["expected_logs"], "spans": gt["totals"]["expected_spans"]}, detail, ok=ok_runlog)
    # a trace opened from the runlog shows its correlated logs
    chain_ids = gt.get("trace_ids", {}).get("chain") or gt.get("trace_ids", {}).get("chain-fail")
    if chain_ids:
        t = chain_ids[0]
        tl = hdx.search("logs", f"TraceId = '{t}'", start_s, end_s)
        ts = hdx.search("traces", f"TraceId = '{t}'", start_s, end_s)
        rep.check("D15b", "a trace id resolves to its spans and correlated logs in HyperDX", [True, True],
                  [len(ts) > 1, len(tl) >= 1])


def hdx_get(hdx, resource):
    for attempt in range(6):
        req = urllib.request.Request(f"{hdx.api}/api/v2/{resource}", headers={"Authorization": f"Bearer {hdx.key}"})
        try:
            return json.load(urllib.request.urlopen(req))["data"]
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(min(int(e.headers.get("Retry-After", "30")), 65) + 1)


def finish(rep, run_id, outdir, gt, delay=None):
    failed = rep.failed()
    summary = {"run_id": run_id, "passed": len(rep.rows) - len(failed), "failed": len(failed),
               "e2e_visible_after_s": delay, "checks": rep.rows}
    with open(os.path.join(outdir, "report.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n== {summary['passed']}/{len(rep.rows)} checks passed  (report: {os.path.join(outdir, 'report.json')})")
    if failed:
        print("FAILED:", ", ".join(r["id"] for r in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
