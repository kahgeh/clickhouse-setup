#!/usr/bin/env python3
from lib import page, quiz, write, neighbours, evidence, esc

gt = evidence("ground-truth.json"); man = evidence("manifest.json"); trace = evidence("trace-sample.json")
tot = gt["totals"]; per = gt["per_endpoint"]
ROUTES = [("ok", 200), ("slow", 200), ("error", 500), ("exception", 500), ("chain", 200), ("chain-fail", 502), ("checkout", 200)]
counts = {r: per[r]["count"] for r, _ in ROUTES}
assert sum(counts.values()) == tot["requests"]
fails = sum(counts[r] for r, s in ROUTES if s >= 500)
assert fails == tot["expected_http_5xx"]
good = tot["requests"] - fails
avail = good / tot["requests"]
assert abs(avail - tot["expected_availability"]) < 1e-12
# illustrative durations (ms): not measured; slow matches the 200 ms in the README's example loadgen command
DUR = {"ok": 5, "slow": 200, "error": 5, "exception": 5, "chain": 6, "chain-fail": 6, "checkout": 60}
LABEL = {"ok": "/ok", "slow": "/slow", "error": "/error", "exception": "/exception", "chain": "/chain", "chain-fail": "/chain-fail", "checkout": "/checkout"}

calc_rows = "\n".join(
    f'<tr><td><code>GET {LABEL[r]}</code></td><td class="num">{s}</td>'
    f'<td><input type="number" min="0" step="1" id="sli-count-{r}" value="{counts[r]}" data-default="{counts[r]}" aria-label="Number of {LABEL[r]} requests"></td>'
    f'<td><input type="number" min="0" step="1" id="sli-ms-{r}" value="{DUR[r]}" data-default="{DUR[r]}" aria-label="Illustrative duration in milliseconds of a {LABEL[r]} request"></td>'
    f'<td class="num" id="sli-r-err-{r}"></td><td class="num" id="sli-r-av-{r}"></td></tr>' for r, s in ROUTES)

tiles = [
    ("Availability (non-5xx / total)", "Number", "Server spans of <code>web</code> without error status, divided by all server spans of <code>web</code>."),
    ("Requests", "Number", "Count of server spans of <code>web</code>."),
    ("Errors (5xx)", "Number", "Count of those server spans with error status. The first alert watches this tile."),
    ("p95 latency (ms)", "Number", "95th percentile of span <code>Duration</code> over the same spans, in milliseconds."),
    ("Availability over time", "Line", "The availability formula per time bucket. A bucket with no traffic is left empty, not drawn as 0%."),
    ("Latency p50 / p95 / p99 (ms)", "Line", "Three percentiles of <code>Duration</code> per time bucket."),
    ("Request rate by route", "Line", "Count of server spans per time bucket, one line per <code>SpanName</code>."),
    ("p95 latency by route (ms)", "Line", "The 95th percentile per time bucket, one line per <code>SpanName</code>."),
    ("Per-route SLI", "Table", "Per <code>SpanName</code>: requests, errors, p95 in milliseconds, and availability as (requests minus errors) divided by requests."),
    ("Error list: failed spans", "Search", "Spans with <code>StatusCode</code> Error: timestamp, service, span name, status message, trace id."),
    ("Error list: error logs", "Search", "Log lines with severity ERROR: timestamp, service, body, trace id."),
    ("app.requests by outcome", "Line", "The metric counter summed by <code>outcome</code>. HyperDX drops each cumulative series' first point, so this tile undercounts, and the span tiles are the reference."),
    ("In-flight requests", "Line", "The up-down counter <code>http.server.active_requests</code>."),
]
tile_rows = "\n".join(f'<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>' for a, b, c in tiles)
assert len(tiles) == 13

dash = man["screens/dash-availability.png"]; runlog = man["screens/dash-runlog.png"]; search = man["screens/search-error-logs.png"]

span_rows = "\n".join(
    f'<tr><td>{esc(s["ServiceName"])}</td><td><code>{esc(s["SpanName"])}</code></td><td>{esc(s["SpanKind"])}</td><td>{esc(s["StatusCode"])}</td><td class="num">{s["duration_ms"]}</td><td><code>{s["ParentSpanId"] or "(empty)"}</code></td></tr>' for s in trace["spans"])
log_rows = "\n".join(
    f'<tr><td>{esc(l["ServiceName"])}</td><td>{esc(l["SeverityText"])}</td><td>{esc(l["Body"])}</td><td><code>{l["SpanId"]}</code></td></tr>' for l in trace["logs"])

body = f'''  <section aria-labelledby="dash">
    <h2 id="dash">The availability and latency dashboard</h2>
    <p>This module explains how to read the dashboards, the error list and the runlog in HyperDX, and how the numbers on them are defined. The dashboard <em>Availability &amp; Latency</em> has 13 tiles. It is created from code, so the definitions below are the ones the screen uses.</p>
    <figure class="shot">
      <img src="../evidence/screens/dash-availability.png" alt="HyperDX dashboard named Availability and Latency with number tiles for availability, requests, errors and p95 latency, line charts over time, a per-route table and error lists">
      <figcaption>The Availability &amp; Latency dashboard in HyperDX, captured from the live stack on {dash["captured_at"]}. The data comes from verification runs whose request mix deliberately includes failing requests ({tot["expected_http_5xx"] / tot["requests"]:.1%} of the requests in the captured run), so availability is well below 100%. The window of this screenshot also holds self-test runs that delete or alter rows on purpose, which is why <code>/ok</code> shows a few errors (see module 3).</figcaption>
    </figure>
    <table class="data">
      <thead><tr><th scope="col">Tile</th><th scope="col">Kind</th><th scope="col">What it computes</th></tr></thead>
      <tbody>
{tile_rows}
      </tbody>
    </table>
    <p class="tag">Source: tile definitions in <code>stack/hyperdx/provision.py</code>.</p>
  </section>

  <section aria-labelledby="defs">
    <h2 id="defs">How availability, rate and percentiles are defined</h2>
    <p>Every number on the first nine tiles comes from one set of spans: the <dfn>server spans</dfn> of the <code>web</code> service, which is one span per request that <code>web</code> received. In the table this is <code>SpanKind = Server</code> and <code>ServiceName = web</code>. The backend's server spans and the client span are not counted, so a request through <code>/chain</code> counts once.</p>
    <ul>
      <li><strong>Request rate</strong> is the count of those spans in a time bucket.</li>
      <li><strong>Error</strong> means the span has error status. The test app sets that status when the response is HTTP 5xx, so <code>/error</code> (500), <code>/exception</code> (500) and <code>/chain-fail</code> (502) are errors and <code>/ok</code>, <code>/slow</code>, <code>/chain</code> and <code>/checkout</code> (200) are not.</li>
      <li><strong>Availability</strong> is the count of spans without error status divided by the count of all those spans.</li>
      <li><strong>Latency</strong> percentiles p50, p95 and p99 are computed from the span <code>Duration</code>, which is stored in nanoseconds. The dashboard divides it by 1,000,000 and shows milliseconds.</li>
    </ul>
    <p>A <code>/slow</code> request that takes longer than expected is still available. Availability here counts failures, and latency is a separate tile.</p>
  </section>

  <section aria-labelledby="calc">
    <h2 id="calc">SLI calculator</h2>
    <p>Set the number of requests of each type and the duration of each type. The calculator applies the definitions above. The defaults are the request counts of the captured verification run, from <code>evidence/ground-truth.json</code>.</p>
    <aside class="caveat"><p><strong>The durations are illustrative.</strong> The evidence does not contain a latency per route. The defaults are made-up round numbers, except that 200 ms for <code>/slow</code> matches the <code>--slow-ms 200</code> in the README's example load command. The percentile is the <dfn>nearest rank</dfn>: sort all N durations ascending and take the value at position ceil(p/100 x N). HyperDX computes its percentiles in ClickHouse, so its values can differ slightly from this definition.</p></aside>
    <div class="sim" id="sli-sim" data-expected-availability="{tot["expected_availability"]!r}" data-expected-total="{tot["requests"]}">
      <h3>Availability and percentiles from counts</h3>
      <div class="controls">
        <label class="ctl">Request mix
          <select id="sli-preset"><option value="captured" selected>The captured run</option><option value="healthy">Healthy traffic, no failures</option><option value="dependency">A failing dependency: every chain request fails</option><option value="custom">Custom (edit the counts)</option></select></label>
      </div>
      <div class="sli-scroll">
      <table class="data">
        <thead><tr><th scope="col">Request</th><th scope="col" class="num">HTTP status</th><th scope="col">Requests</th><th scope="col">Duration (ms, illustrative)</th><th scope="col" class="num">5xx errors</th><th scope="col" class="num">Availability</th></tr></thead>
        <tbody>
{calc_rows}
        </tbody>
      </table>
      </div>
      <div class="sli-out out" aria-live="polite">
        <div class="kpi"><span class="big" id="sli-avail">{avail:.2%}</span><span class="what">availability</span></div>
        <div class="kpi"><span class="big" id="sli-total">{tot["requests"]}</span><span class="what">requests</span></div>
        <div class="kpi"><span class="big" id="sli-errors">{fails}</span><span class="what">errors (5xx)</span></div>
        <div class="kpi"><span class="big" id="sli-p50"></span><span class="what">p50, illustrative</span></div>
        <div class="kpi"><span class="big" id="sli-p95"></span><span class="what">p95, illustrative</span></div>
        <div class="kpi"><span class="big" id="sli-p99"></span><span class="what">p99, illustrative</span></div>
      </div>
      <p class="out" id="sli-line" aria-live="polite">{good} non-5xx requests out of {tot["requests"]} is {avail:.2%}. {fails} failed with a 5xx status.</p>
      <p class="readout" id="sli-check">Inputs equal the captured run.</p>
    </div>
    <div class="evid">
      <h3>Worked check against the evidence</h3>
      <p>The captured run sent {" + ".join(str(counts[r]) for r, _ in ROUTES)} = {tot["requests"]} requests. The failing types are <code>/error</code> ({counts["error"]}), <code>/exception</code> ({counts["exception"]}) and <code>/chain-fail</code> ({counts["chain-fail"]}), which is {fails} requests, matching <code>expected_http_5xx</code> = {tot["expected_http_5xx"]}. Then {good} of {tot["requests"]} succeeded, and {good} / {tot["requests"]} = {avail:.6f}, which is the <code>expected_availability</code> in the file ({tot["expected_availability"]:.6f}). With the default inputs the calculator shows {avail:.2%}.</p>
      <p class="src">Source: <code>evidence/ground-truth.json</code>, run <code>{gt["run_id"]}</code>. Expectations come from the contract table, not from observed telemetry, and module 9 shows the check that compares them with ClickHouse.</p>
    </div>
  </section>

  <section aria-labelledby="errors">
    <h2 id="errors">The error list</h2>
    <p>The dashboard has two error lists, and each is also a saved search: <em>Error list: failed spans</em> (spans with error status) and <em>Error list: error logs</em> (log lines with severity ERROR). The screenshot shows the log search.</p>
    <figure class="shot">
      <img src="../evidence/screens/search-error-logs.png" alt="HyperDX saved search named Error list: error logs, with a histogram of error counts over time and a table of log lines with timestamp, service, body and trace id">
      <figcaption>The saved search <em>Error list: error logs</em>, captured on {search["captured_at"]}. The page shows 125 results, 8,106 scanned rows and 76 ms elapsed.</figcaption>
    </figure>
    <p>Each row tells you four things: when the line was written, which service wrote it, what it said, and the trace id of the request it belongs to. The service and body show where and how it failed. In the screenshot, a <code>web</code> line <em>chain-fail: backend ...</em> and a <code>backend</code> line <em>backend failure</em> have the same trace id. They are the two ERROR lines of one <code>/chain-fail</code> request, one from each service. A row does not say which test run it came from and does not say whether the failure is still happening.</p>
  </section>

  <section aria-labelledby="link">
    <h2 id="link">From a log line to its spans</h2>
    <p>The trace id is the join. Copy the trace id from an error row, search spans for that <code>TraceId</code>, and you get the request's server span, client span and backend spans, with the status and duration of each. Search logs for the same id to read every line the request wrote. Module 4 shows the same relationship on one captured trace. The span id on a log line then names the span that wrote it.</p>
  </section>

  <section aria-labelledby="runlog">
    <h2 id="runlog">Following one test run</h2>
    <p>Every request the load generator sends carries a run id, and the app records it as the attribute <code>test_run_id</code> on spans, log records and metrics. The <em>Runlog</em> dashboard has four tiles and a filter on that attribute: a requests-by-endpoint-and-status table, a timeline of spans per service, the logs of the run, and the spans of the run. Select the run id in the filters and all four tiles show that run only. There are two filters, one for logs and one for traces, because the attribute lives in <code>LogAttributes</code> on logs and in <code>SpanAttributes</code> on spans.</p>
    <figure class="shot">
      <img src="../evidence/screens/dash-runlog.png" alt="HyperDX dashboard named Runlog with two test_run_id filters, a table of requests by endpoint and status, a run timeline, run logs and run spans">
      <figcaption>The Runlog dashboard, captured on {runlog["captured_at"]}. No run id is selected in the two filters in this capture, so the tiles count every run in the time range.</figcaption>
    </figure>
  </section>

  <section aria-labelledby="model">
    <h2 id="model">The columns you will read</h2>
    <p>Spans are rows in <code>otel_traces</code> and log lines are rows in <code>otel_logs</code>. For the dashboards and searches in this module you need these columns.</p>
    <table class="data">
      <thead><tr><th scope="col">Table</th><th scope="col">Column</th><th scope="col">Meaning</th></tr></thead>
      <tbody>
        <tr><td>otel_traces</td><td><code>SpanName</code></td><td>The operation, for example <code>GET /chain</code>.</td></tr>
        <tr><td>otel_traces</td><td><code>SpanKind</code></td><td>Server, Client or Internal. The dashboard counts Server spans of <code>web</code>.</td></tr>
        <tr><td>otel_traces</td><td><code>StatusCode</code></td><td>Unset or Error. Error is what the availability formula treats as a failure.</td></tr>
        <tr><td>otel_traces</td><td><code>Duration</code></td><td>Span length in nanoseconds. The dashboards show milliseconds.</td></tr>
        <tr><td>otel_traces</td><td><code>TraceId</code>, <code>ParentSpanId</code></td><td>The request, and the span that called this one. The root span's parent is empty.</td></tr>
        <tr><td>otel_logs</td><td><code>Body</code></td><td>The log message.</td></tr>
        <tr><td>otel_logs</td><td><code>SeverityText</code></td><td>INFO, ERROR and so on.</td></tr>
        <tr><td>otel_logs</td><td><code>LogAttributes</code></td><td>A map of extra attributes, such as <code>test_run_id</code>. Spans have the equivalent <code>SpanAttributes</code>.</td></tr>
      </tbody>
    </table>
    <div class="evid">
      <h3>Real rows of one request</h3>
      <p>These are the spans and logs of the captured <code>GET /chain</code> request, trace id <code>{trace["trace_id"]}</code>. The durations are shown in milliseconds.</p>
      <div class="sli-scroll">
      <table class="data">
        <thead><tr><th scope="col">ServiceName</th><th scope="col">SpanName</th><th scope="col">SpanKind</th><th scope="col">StatusCode</th><th scope="col" class="num">Duration (ms)</th><th scope="col">ParentSpanId</th></tr></thead>
        <tbody>
{span_rows}
        </tbody>
      </table>
      <table class="data">
        <thead><tr><th scope="col">ServiceName</th><th scope="col">SeverityText</th><th scope="col">Body</th><th scope="col">SpanId</th></tr></thead>
        <tbody>
{log_rows}
        </tbody>
      </table>
      </div>
      <p class="src">Source: ClickHouse <code>otel.otel_traces</code> and <code>otel.otel_logs</code>, captured {man["trace-sample.json"]["captured_at"]} (<code>evidence/trace-sample.json</code>).</p>
    </div>
  </section>

  <section aria-labelledby="cost">
    <h2 id="cost">Why searches should be time-bounded and field-filtered</h2>
    <p>A search in a scan-priced system is billed on the data it reads, so a wide time range costs more even when it finds the same lines (module 1 shows the price model). In ClickHouse the log and trace tables are partitioned by day and have the service and the time in their sort key, so a short time range and a filter on the service let it skip most of the data it would otherwise read, and the cost shows up as a busier server and not as a larger bill. Both habits also make searches faster in either system. The screenshot above shows the kind of figure to look at: rows scanned compared with results returned. Module 1 has the explorer for the cost comparison, with its assumptions.</p>
    <p><a href="01-problem-and-outcome.html">Back to module 1: the problem and the outcome</a></p>
  </section>'''

q = quiz([
    {"q": "A one-minute window contains 100 <code>/ok</code>, 30 <code>/chain</code>, 20 <code>/error</code> and 10 <code>/chain-fail</code> requests and nothing else. What does the availability tile show for it?",
     "options": [
        ("81.25%: 130 of the 160 requests are not 5xx, so 30 failed.", "The three 5xx types are the failures, and the formula divides everything else by the total."),
        ("87.5%, because only the 20 <code>/error</code> requests failed.", "A 502 is a 5xx status as well, so revisit which responses the error status covers."),
        ("62.5%, because only <code>/ok</code> requests count as available.", "Every request without a 5xx status is available, so revisit what the numerator contains."),
     ], "answer": 0},
    {"q": "An error-list row shows service <code>backend</code>, body <em>backend failure</em> and a trace id. You need to know what the user-facing request did. What do you do with the row?",
     "options": [
        ("Read the test run id from the row to find the run, then read the run's totals.", "The error list does not show the run id as a column, so revisit what each row contains."),
        ("Conclude that only the backend was affected, because only the backend wrote the line.", "A line records where something was logged and not how far the effect travelled, so look at the other spans of the request."),
        ("Look the trace id up in the spans and the logs of both services.", "The trace id is the key that groups everything the request produced, across both services."),
     ], "answer": 2},
    {"q": "Two load runs overlapped in time on the same stack and you want the failed requests of run B only. What gives you exactly those?",
     "options": [
        ("Narrow the dashboard's time range to the start and end of run B.", "Where the runs overlap, the range still includes requests of run A, so revisit what separates the two runs."),
        ("Set the runlog's <code>test_run_id</code> filters to run B's id and read the failures.", "The attribute is recorded on every span and log record of a run, so a filter on it separates the runs however their times overlap."),
        ("Filter on the service <code>web</code>, because each run uses its own service name.", "Both runs use the same service names, so that filter cannot tell the runs apart."),
     ], "answer": 1},
], gate="All correct. Continue to module 6 to see how alerts are evaluated on the data you just read.")

prev, nxt = neighbours("m05")
write("05-finding-your-data.html", page(
    "05-finding-your-data.html", "m05", "Finding your data", "Using it", "2 of 3",
    "How to read the availability and latency dashboard, find failed requests and their logs, and follow one test run, with the exact definition behind every number.",
    ["read the availability and latency dashboard and say how each number is defined,",
     "find the failed requests and the log lines that belong to them, and",
     "use the runlog to follow one test run."],
    body, q, scripts=["sim-sli.js"], prev=prev, next=nxt))
