#!/usr/bin/env python3
"""Provision HyperDX (connection, sources, dashboards, saved searches) through its API.

Standard library only. Idempotent: every object is upserted by name, so it is safe to
re-run. Environment (dev defaults shown; override for any other environment):

  HDX_UI        http://localhost:8080   session-auth API (register/login/me)
  HDX_API       http://localhost:8000   external API v2 (Bearer personal access key)
  HDX_EMAIL     admin@local.dev         DEV ONLY credentials for the first user
  HDX_PASSWORD  LocalDevPass1!          DEV ONLY
  CH_HOST       http://clickhouse:8123  ClickHouse URL as seen from the HyperDX container
  CH_USER       hyperdx_ro              read-only ClickHouse user
  CH_PASSWORD   hyperdx_ro_dev

Usage:
  provision.py                     # base content (connection, sources, dashboards, searches)
  provision.py runlog RUN_ID       # additionally (re)create saved searches scoped to one test run
Writes ids to stack/hyperdx/state.json for the verification script.
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request

UI = os.environ.get("HDX_UI", "http://localhost:8080").rstrip("/")
API = os.environ.get("HDX_API", "http://localhost:8000").rstrip("/")
EMAIL = os.environ.get("HDX_EMAIL", "admin@local.dev")
PASSWORD = os.environ.get("HDX_PASSWORD", "LocalDevPass1!")
CH_HOST = os.environ.get("CH_HOST", "http://clickhouse:8123")
CH_USER = os.environ.get("CH_USER", "hyperdx_ro")
CH_PASSWORD = os.environ.get("CH_PASSWORD", "hyperdx_ro_dev")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

DB = "otel"
# Web-tier request SLI scope: server spans of the `web` service. Error <=> HTTP 5xx.
WEB_SERVER = "SpanKind:Server AND ServiceName:web"
WEB_ERR = f"{WEB_SERVER} AND StatusCode:Error"
WEB_OK = f"{WEB_SERVER} AND NOT StatusCode:Error"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Don't follow redirects: the login 303 carries the session Set-Cookie."""

    def redirect_request(self, *args, **kwargs):
        return None


class Session:
    """Minimal cookie handling (http.cookiejar mishandles cookies scoped to `localhost`)."""

    def __init__(self):
        self.cookies = {}
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _store(self, headers):
        for raw in headers.get_all("Set-Cookie") or []:
            name, _, rest = raw.partition("=")
            self.cookies[name.strip()] = rest.split(";", 1)[0]

    def call(self, method, url, body=None, headers=None, ok=(200, 201, 204, 303)):
        for attempt in range(8):
            try:
                return self._call(method, url, body, headers, ok)
            except RuntimeError as e:
                if "HTTP 429" not in str(e) or attempt == 7:
                    raise
                time.sleep(3 * (attempt + 1))  # HyperDX rate-limits bursts; back off

    def _call(self, method, url, body=None, headers=None, ok=(200, 201, 204, 303)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with self.opener.open(req, timeout=30) as r:
                raw, code, hdrs = r.read().decode(), r.status, r.headers
        except urllib.error.HTTPError as e:
            raw, code, hdrs = e.read().decode(), e.code, e.headers
        self._store(hdrs)
        if code not in ok:
            raise RuntimeError(f"{method} {url} -> HTTP {code}: {raw[:600]}")
        return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def wait_ready(s, attempts=90):
    for _ in range(attempts):
        try:
            return s.call("GET", f"{UI}/api/installation")
        except Exception:
            time.sleep(2)
    raise SystemExit("HyperDX did not become ready")


def authenticate(s):
    inst = wait_ready(s)
    if not inst.get("isTeamExisting"):
        s.call("POST", f"{UI}/api/register/password",
               {"email": EMAIL, "password": PASSWORD, "confirmPassword": PASSWORD})
    else:
        s.call("POST", f"{UI}/api/login/password", {"email": EMAIL, "password": PASSWORD})
    me = s.call("GET", f"{UI}/api/me")
    return me["accessKey"]


def keep_ids(existing, body):
    """On update HyperDX requires dashboard filter ids; reuse existing ones by name
    (and tile ids by tile name) so re-provisioning updates in place."""
    body = json.loads(json.dumps(body))
    old_filters = {f.get("name"): f.get("id") for f in existing.get("filters", [])}
    for i, f in enumerate(body.get("filters", [])):
        f["id"] = old_filters.get(f["name"]) or secrets.token_hex(12)
    old_tiles = {t.get("name"): t.get("id") for t in existing.get("tiles", [])}
    for t in body.get("tiles", []):
        if old_tiles.get(t["name"]):
            t["id"] = old_tiles[t["name"]]
    return body


class V2:
    def __init__(self, s, key):
        self.s, self.h = s, {"Authorization": f"Bearer {key}"}

    def list(self, resource):
        out = self.s.call("GET", f"{API}/api/v2/{resource}", headers=self.h)
        return out["data"] if isinstance(out, dict) else out

    def upsert(self, resource, name, body):
        """Create or update the object called `name`; returns its id."""
        body = {**body, "name": name}
        existing = next((o for o in self.list(resource) if o.get("name") == name), None)
        if existing and resource == "dashboards":
            body = keep_ids(existing, body)
        if existing:
            out = self.s.call("PUT", f"{API}/api/v2/{resource}/{existing['id']}", body, self.h)
        else:
            out = self.s.call("POST", f"{API}/api/v2/{resource}", body, self.h)
        data = out.get("data", out) if isinstance(out, dict) else out
        return data["id"]

    def post(self, path, body):
        return self.s.call("POST", f"{API}/api/v2/{path}", body, self.h)


# ---------------------------------------------------------------- sources


def source_bodies(conn_id):
    logs = {
        "kind": "log", "name": "Logs", "connection": conn_id,
        "from": {"databaseName": DB, "tableName": "otel_logs"},
        "timestampValueExpression": "Timestamp",
        "defaultTableSelectExpression": "Timestamp, ServiceName, SeverityText, Body",
        "serviceNameExpression": "ServiceName", "severityTextExpression": "SeverityText",
        "bodyExpression": "Body", "traceIdExpression": "TraceId", "spanIdExpression": "SpanId",
        "resourceAttributesExpression": "ResourceAttributes",
        "eventAttributesExpression": "LogAttributes",
    }
    traces = {
        "kind": "trace", "name": "Traces", "connection": conn_id,
        "from": {"databaseName": DB, "tableName": "otel_traces"},
        "timestampValueExpression": "Timestamp",
        "defaultTableSelectExpression": "Timestamp, ServiceName, StatusCode, round(Duration/1e6, 2) AS duration_ms, TraceId, SpanId, ParentSpanId, SpanName",
        "durationExpression": "Duration", "durationPrecision": 9,
        "traceIdExpression": "TraceId", "spanIdExpression": "SpanId",
        "parentSpanIdExpression": "ParentSpanId", "spanNameExpression": "SpanName",
        "spanKindExpression": "SpanKind", "serviceNameExpression": "ServiceName",
        "statusCodeExpression": "StatusCode", "statusMessageExpression": "StatusMessage",
        "resourceAttributesExpression": "ResourceAttributes",
        "eventAttributesExpression": "SpanAttributes",
    }
    metrics = {
        "kind": "metric", "name": "Metrics", "connection": conn_id,
        "from": {"databaseName": DB, "tableName": ""},
        "metricTables": {"gauge": "otel_metrics_gauge", "sum": "otel_metrics_sum",
                         "histogram": "otel_metrics_histogram"},
        "timestampValueExpression": "TimeUnix",
        "resourceAttributesExpression": "ResourceAttributes",
    }
    return {"logs": logs, "traces": traces, "metrics": metrics}


# ---------------------------------------------------------------- dashboards


def sel(agg, where=None, field=None, alias=None, level=None):
    item = {"aggFn": agg}
    if field:
        item["valueExpression"] = field
    if where:
        item["where"], item["whereLanguage"] = where, "lucene"
    if alias:
        item["alias"] = alias
    if level is not None:
        item["aggFn"], item["level"] = "quantile", level
    return item


MS = "Duration / 1000000"
PCT = {"output": "percent", "mantissa": 3}
MS_FMT = {"output": "number", "mantissa": 1, "unit": "ms"}


def tile(name, x, y, w, h, config):
    return {"name": name, "x": x, "y": y, "w": w, "h": h, "config": config}


def availability_select():
    return [sel("count", WEB_OK, alias="ok"), sel("count", WEB_SERVER, alias="total")]


def latency_quantiles():
    return [sel("quantile", WEB_SERVER, MS, f"p{int(q * 100)} ms", level=q) for q in (0.5, 0.95, 0.99)]


def availability_dashboard(src):
    t, m, lg = src["traces"], src["metrics"], src["logs"]
    avail = {"expression": "A / B", "alias": "Availability", "numberFormat": PCT}
    return {
        "name": "Availability & Latency",
        "tags": ["sre"],
        "tiles": [
            tile("Availability (non-5xx / total)", 0, 0, 6, 3, {
                "displayType": "number", "sourceId": t, "select": availability_select(),
                "formulas": [avail]}),
            tile("Requests", 6, 0, 6, 3, {
                "displayType": "number", "sourceId": t, "select": [sel("count", WEB_SERVER)]}),
            tile("Errors (5xx)", 12, 0, 6, 3, {
                "displayType": "number", "sourceId": t, "select": [sel("count", WEB_ERR)]}),
            tile("p95 latency (ms)", 18, 0, 6, 3, {
                "displayType": "number", "sourceId": t, "numberFormat": MS_FMT,
                "select": [sel("quantile", WEB_SERVER, MS, level=0.95)]}),
            tile("Availability over time", 0, 3, 12, 6, {
                "displayType": "line", "sourceId": t, "select": availability_select(),
                # No traffic is "no data", not 0% availability: do not fill empty buckets with zero.
                "formulas": [avail], "showOperandSeries": False, "fillNulls": False}),
            tile("Latency p50 / p95 / p99 (ms)", 12, 3, 12, 6, {
                "displayType": "line", "sourceId": t, "select": latency_quantiles(),
                "numberFormat": MS_FMT}),
            tile("Request rate by route", 0, 9, 12, 6, {
                "displayType": "line", "sourceId": t, "select": [sel("count", WEB_SERVER)],
                "groupBy": "SpanName"}),
            tile("p95 latency by route (ms)", 12, 9, 12, 6, {
                "displayType": "line", "sourceId": t, "numberFormat": MS_FMT,
                "select": [sel("quantile", WEB_SERVER, MS, level=0.95)], "groupBy": "SpanName"}),
            tile("Per-route SLI", 0, 15, 24, 7, {
                "displayType": "table", "sourceId": t, "groupBy": "SpanName",
                "select": [sel("count", WEB_SERVER, alias="requests"),
                           sel("count", WEB_ERR, alias="errors"),
                           sel("quantile", WEB_SERVER, MS, "p95 ms", level=0.95)],
                "formulas": [{"expression": "(A - B) / A", "alias": "availability",
                              "numberFormat": PCT}]}),
            tile("Error list: failed spans", 0, 22, 12, 8, {
                "displayType": "search", "sourceId": t, "whereLanguage": "lucene",
                "where": "StatusCode:Error",
                "select": "Timestamp, ServiceName, SpanName, StatusMessage, TraceId"}),
            tile("Error list: error logs", 12, 22, 12, 8, {
                "displayType": "search", "sourceId": lg, "whereLanguage": "lucene",
                "where": "SeverityText:ERROR",
                "select": "Timestamp, ServiceName, Body, TraceId"}),
            tile("app.requests by outcome (sum; HyperDX drops each series first cumulative point, so it undercounts)", 0, 30, 12, 6, {
                "displayType": "line", "sourceId": m, "groupBy": "Attributes['outcome']",
                "select": [{"aggFn": "sum", "valueExpression": "Value",
                            "metricName": "app.requests", "metricType": "sum"}]}),
            tile("In-flight requests (gauge/updown)", 12, 30, 12, 6, {
                "displayType": "line", "sourceId": m,
                "select": [{"aggFn": "sum", "valueExpression": "Value",
                            "metricName": "http.server.active_requests", "metricType": "sum"}]}),
        ],
    }


def runlog_dashboard(src):
    t, lg = src["traces"], src["logs"]
    return {
        "name": "Runlog",
        "tags": ["sre"],
        "filters": [
            {"type": "QUERY_EXPRESSION", "name": "test_run_id (logs)",
             "expression": "LogAttributes['test_run_id']", "sourceId": lg,
             "appliesToSourceIds": [lg]},
            {"type": "QUERY_EXPRESSION", "name": "test_run_id (traces)",
             "expression": "SpanAttributes['test_run_id']", "sourceId": t,
             "appliesToSourceIds": [t]},
        ],
        "tiles": [
            tile("Requests by endpoint and status", 0, 0, 12, 6, {
                "displayType": "table", "sourceId": t, "groupBy": "SpanName, StatusCode",
                "select": [sel("count", "SpanKind:Server AND ServiceName:web", alias="requests")]}),
            tile("Run timeline (spans)", 12, 0, 12, 6, {
                "displayType": "line", "sourceId": t, "select": [sel("count")], "groupBy": "ServiceName"}),
            tile("Run logs", 0, 6, 24, 10, {
                "displayType": "search", "sourceId": lg, "whereLanguage": "lucene", "where": "",
                "select": "Timestamp, ServiceName, SeverityText, Body, TraceId"}),
            tile("Run spans", 0, 16, 24, 10, {
                "displayType": "search", "sourceId": t, "whereLanguage": "lucene", "where": "",
                "select": "Timestamp, ServiceName, SpanName, StatusCode, TraceId, SpanId, ParentSpanId"}),
        ],
    }


# ---------------------------------------------------------------- saved searches


def saved_searches(src):
    return {
        "Error list: error logs": {
            "sourceId": src["logs"], "whereLanguage": "lucene", "where": "SeverityText:ERROR",
            "select": "Timestamp, ServiceName, Body, TraceId", "orderBy": "Timestamp DESC",
            "tags": ["errors"]},
        "Error list: failed spans": {
            "sourceId": src["traces"], "whereLanguage": "lucene", "where": "StatusCode:Error",
            "select": "Timestamp, ServiceName, SpanName, StatusMessage, TraceId",
            "orderBy": "Timestamp DESC", "tags": ["errors"]},
        "Runlog: logs of a run (edit test_run_id)": {
            "sourceId": src["logs"], "whereLanguage": "sql",
            "where": "LogAttributes['test_run_id'] = 'EDIT_ME'",
            "select": "Timestamp, ServiceName, SeverityText, Body, TraceId",
            "orderBy": "Timestamp ASC", "tags": ["runlog"]},
        "Runlog: spans of a run (edit test_run_id)": {
            "sourceId": src["traces"], "whereLanguage": "sql",
            "where": "SpanAttributes['test_run_id'] = 'EDIT_ME'",
            "select": "Timestamp, ServiceName, SpanName, StatusCode, TraceId, SpanId, ParentSpanId",
            "orderBy": "Timestamp ASC", "tags": ["runlog"]},
    }


def run_searches(src, run_id):
    return {
        f"Runlog {run_id}: logs": {
            "sourceId": src["logs"], "whereLanguage": "sql",
            "where": f"LogAttributes['test_run_id'] = '{run_id}'",
            "select": "Timestamp, ServiceName, SeverityText, Body, TraceId",
            "orderBy": "Timestamp ASC", "tags": ["runlog", run_id[:30]]},
        f"Runlog {run_id}: spans": {
            "sourceId": src["traces"], "whereLanguage": "sql",
            "where": f"SpanAttributes['test_run_id'] = '{run_id}'",
            "select": "Timestamp, ServiceName, SpanName, StatusCode, TraceId, SpanId, ParentSpanId",
            "orderBy": "Timestamp ASC", "tags": ["runlog", run_id[:30]]},
    }


# ---------------------------------------------------------------- alerts

SINK_URL = os.environ.get("ALERT_WEBHOOK_URL", "http://webhook-sink:8089/hook")
ALERT_THRESHOLD = 4  # strictly MORE than 4 failures within the 1-minute window
# HyperDX 2.40: "above" means >= threshold; "above_exclusive" means > threshold. The alerts are named
# "above 4", so the exclusive form is the one that matches (override only for negative tests).
ALERT_THRESHOLD_TYPE = os.environ.get("ALERT_THRESHOLD_TYPE", "above_exclusive")


def webhook_body():
    return {
        "name": "alert-sink", "service": "generic", "url": SINK_URL,
        "description": "Records alert deliveries for verification (stack/verify/alerts.py)",
        # Raw text is recorded by the sink, so JSON validity of interpolated values does not matter there.
        "body": '{"title": "{{title}}", "state": "{{state}}", "body": "{{body}}", "link": "{{link}}"}',
    }


def alert_bodies(webhook_id, dashboards, searches, v2):
    dash = next(d for d in v2.list("dashboards") if d["id"] == dashboards["Availability & Latency"])
    tile = next(t for t in dash["tiles"] if t["name"] == "Errors (5xx)")
    channel = {"type": "webhook", "webhookId": webhook_id}
    common = {"threshold": ALERT_THRESHOLD, "thresholdType": ALERT_THRESHOLD_TYPE, "interval": "1m", "channels": [channel]}
    return {
        "Alert: 5xx errors above 4 per minute (dashboard tile)": {
            **common, "source": "tile", "dashboardId": dash["id"], "tileId": tile["id"],
            "message": "More than 4 web requests failed with 5xx in the last minute"},
        "Alert: ERROR logs above 4 per minute (saved search)": {
            **common, "source": "saved_search", "savedSearchId": searches["Error list: error logs"],
            "message": "More than 4 ERROR log lines in the last minute"},
    }


def provision_alerts(v2, dashboards, searches):
    webhook_id = v2.upsert("webhooks", "alert-sink", webhook_body())
    ids = {}
    for name, body in alert_bodies(webhook_id, dashboards, searches, v2).items():
        ids[name] = v2.upsert("alerts", name, {**body, "name": name})
    return webhook_id, ids


# ---------------------------------------------------------------- main


def runlog_only(run_id):
    """Cheap path used by the verifier: upsert just this run's saved searches from cached state."""
    with open(STATE) as f:
        state = json.load(f)
    v2 = V2(Session(), state["accessKey"])
    for n, b in run_searches(state["sources"], run_id).items():
        state["savedSearches"][n] = v2.upsert("saved-searches", n, b)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    run_id = sys.argv[2] if len(sys.argv) >= 3 and sys.argv[1] == "runlog" else None
    if run_id and os.path.exists(STATE):
        return runlog_only(run_id)
    s = Session()
    key = authenticate(s)
    v2 = V2(s, key)

    conn_id = v2.upsert("connections", "clickhouse", {
        "name": "clickhouse", "host": CH_HOST, "username": CH_USER, "password": CH_PASSWORD})
    src = {k: v2.upsert("sources", b["name"], b) for k, b in source_bodies(conn_id).items()}

    dashboards = {}
    for body in (availability_dashboard(src), runlog_dashboard(src)):
        v2.post("dashboards/validate", body)  # fail early with HyperDX's own validation message
        dashboards[body["name"]] = v2.upsert("dashboards", body["name"], body)

    searches = {n: v2.upsert("saved-searches", n, b) for n, b in saved_searches(src).items()}
    if run_id:
        searches.update({n: v2.upsert("saved-searches", n, b)
                         for n, b in run_searches(src, run_id).items()})

    webhook_id, alerts = provision_alerts(v2, dashboards, searches)

    state = {"api": API, "ui": UI, "accessKey": key, "connectionId": conn_id, "sources": src,
             "dashboards": dashboards, "savedSearches": searches,
             "webhookId": webhook_id, "alerts": alerts}
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)
    print(json.dumps({k: state[k] for k in ("connectionId", "sources", "dashboards", "savedSearches", "alerts")},
                     indent=2))


if __name__ == "__main__":
    main()
