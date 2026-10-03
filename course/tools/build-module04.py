#!/usr/bin/env python3
import json, os
from lib import page, quiz, write, neighbours, evidence, esc

trace = evidence("trace-sample.json"); man = evidence("manifest.json")["trace-sample.json"]
tid = trace["trace_id"]
spans = trace["spans"]; logs = trace["logs"]
by_id = {s["SpanId"]: s for s in spans}
web_client = next(s for s in spans if s["ServiceName"] == "web" and s["SpanKind"] == "Client")
backend_server = next(s for s in spans if s["ServiceName"] == "backend" and s["SpanKind"] == "Server")
assert backend_server["ParentSpanId"] == web_client["SpanId"]

# The /chain-fail shape comes from the per-request contract (app/README.md), not from a captured trace.
fail = {
    "trace_id": "not captured (shape from the contract)",
    "label": "Contract shape for GET /chain-fail (HTTP 502): not a captured trace. Ids, durations and log bodies are not available.",
    "spans": [
        {"ServiceName": "web", "SpanKind": "Server", "SpanName": "GET /chain-fail", "StatusCode": "Error", "duration_ms": None, "SpanId": "", "ParentSpanId": "", "depth": 0},
        {"ServiceName": "web", "SpanKind": "Client", "SpanName": "backend.Process", "StatusCode": "Error", "duration_ms": None, "SpanId": "", "ParentSpanId": "", "depth": 1, "parent_note": "the span above it (contract shape)"},
        {"ServiceName": "backend", "SpanKind": "Server", "SpanName": "backend.Process", "StatusCode": "Error", "duration_ms": None, "SpanId": "", "ParentSpanId": "", "depth": 2, "parent_note": "the span above it (contract shape)"},
    ],
    "logs": [
        {"ServiceName": "web", "SeverityText": "ERROR", "Body": "", "SpanId": "", "for_kind": "Server"},
        {"ServiceName": "backend", "SeverityText": "ERROR", "Body": "", "SpanId": "", "for_kind": "Server"},
    ],
}
chain = {"trace_id": tid, "label": f'Captured trace of one GET /chain request, read from ClickHouse on {man["captured_at"]} ({man["source"]}).',
         "spans": spans, "logs": logs}
data_json = json.dumps({"chain": chain, "fail": fail})

span_rows = "\n".join(
    f'<tr><td>{esc(s["ServiceName"])}</td><td>{esc(s["SpanKind"])}</td><td><code>{esc(s["SpanName"])}</code></td><td>{esc(s["StatusCode"])}</td>'
    f'<td class="num">{s["duration_ms"]}</td><td><code>{s["SpanId"]}</code></td><td><code>{s["ParentSpanId"] or "none"}</code></td></tr>' for s in spans)
log_rows = "\n".join(
    f'<tr><td>{esc(l["ServiceName"])}</td><td>{esc(l["SeverityText"])}</td><td>{esc(l["Body"])}</td><td><code>{l["SpanId"]}</code></td><td>{esc(by_id[l["SpanId"]]["SpanKind"])} span <code>{esc(by_id[l["SpanId"]]["SpanName"])}</code></td></tr>' for l in logs)

contract = [
    ("<code>GET /ok</code>", "200", "1: web SERVER <code>GET /ok</code>", "1 INFO"),
    ("<code>GET /slow?ms=N</code>", "200 after N ms (default 200)", "1: web SERVER <code>GET /slow</code>", "1 INFO"),
    ("<code>GET /error</code>", "500", "1: web SERVER <code>GET /error</code>, status Error", "1 ERROR"),
    ("<code>GET /exception</code>", "500", "1: web SERVER <code>GET /exception</code>, status Error, with an <code>exception</code> event (<code>exception.type</code>, <code>exception.message</code>)", "1 ERROR"),
    ("<code>GET /chain</code>", "200", "4: web SERVER <code>GET /chain</code>, web CLIENT <code>backend.Process</code>, backend SERVER <code>backend.Process</code>, backend INTERNAL <code>backend.compute</code>", "2 INFO (web, backend)"),
    ("<code>GET /chain-fail</code>", "502", "3: web SERVER, web CLIENT, backend SERVER, all status Error", "2 ERROR (web, backend)"),
    ("<code>GET /checkout</code>", "200", "22: the fictitious checkout flow, function-level spans in web and backend (see above)", "2 INFO (web, backend)"),
    ("<code>GET /health</code>", "200", "none", "none (no metrics either)"),
]
contract_rows = "\n".join(f'<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>' for a, b, c, d in contract)

# ---- function-level detail: the captured /checkout trace, rendered as an indented tree
co = evidence("checkout-trace-sample.json"); co_man = evidence("manifest.json")["checkout-trace-sample.json"]
assert os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence", "screens", "trace-waterfall-checkout.png"))
co_spans = co["spans"]
kids = {}
for sp in co_spans:
    kids.setdefault(sp["ParentSpanId"], []).append(sp)
def tree(sp, prefix="", last=True, root=True):
    extra = ""
    if sp.get("items"):
        extra = f'  items={sp["items"]} slowest_line_ms={sp["slowest_line_ms"]}'
    line = ("" if root else prefix + ("└─ " if last else "├─ ")) + f'{sp["SpanName"]}  [{sp["ServiceName"]}, {float(sp["duration_ms"]):.1f} ms]{extra}'
    out = [line]
    cs = kids.get(sp["SpanId"], [])
    for i, c in enumerate(cs):
        out += tree(c, prefix + ("" if root else ("   " if last else "│  ")), i == len(cs) - 1, False)
    return out
co_root = next(sp for sp in co_spans if sp["ParentSpanId"] == "")
co_tree = esc("\n".join(tree(co_root)))
co_count = len(co_spans)
co_price = next(sp for sp in co_spans if sp["SpanName"] == "price_items")

body = f'''  <section aria-labelledby="smallest">
    <h2 id="smallest">The smallest example: GET /chain</h2>
    <p>This module explains what an application has to send so that one request can be found again as a set of spans and log lines. The example is a request to <code>GET /chain</code> on the test app's <code>web</code> service. The web service calls the <code>backend</code> service over gRPC and answers 200. That one request produces four spans in a single trace and two log lines, and the response carries the trace identifier in an <code>x-trace-id</code> header.</p>
    <p>Three things have to be true for that to work. Each service says who it is and where it runs (resource attributes). The trace context travels from the web service to the backend with the call. Each log line is written with the identifiers of the span that was active when it was written. The next sections take them in turn, and the trace explorer shows the result for a real request.</p>
  </section>

  <section aria-labelledby="resource">
    <h2 id="resource">Resource attributes: who and where</h2>
    <p>A <dfn>resource attribute</dfn> describes the process that produced the telemetry, and it is attached to every span, log record and metric that process sends. The test app sets three.</p>
    <table class="data">
      <thead><tr><th scope="col">Attribute</th><th scope="col">Value in the test app</th><th scope="col">Used for</th></tr></thead>
      <tbody>
        <tr><td><code>service.name</code></td><td><code>web</code> or <code>backend</code> (from <code>SERVICE_NAME</code>, with those defaults)</td><td>Which service wrote this row. Dashboards and the error list group and filter on it.</td></tr>
        <tr><td><code>service.version</code></td><td>the version of the app's crate</td><td>Which build was running, so a change in behaviour can be tied to a release.</td></tr>
        <tr><td><code>deployment.environment.name</code></td><td><code>local</code></td><td>Which environment the data belongs to.</td></tr>
      </tbody>
    </table>
    <p>The environment attribute matters when several environments report to one place. A filter such as <code>deployment.environment.name = production</code> only works if every service spells the value the same way. If the web service reports <code>production</code> and the backend reports <code>prod</code>, the filter returns the web spans and silently drops the backend spans of the same requests. Choose the values once, and set them in the same place for every service.</p>
    <aside class="note"><p><strong>Only one environment was run here.</strong> The test app always reports <code>local</code>. The advice about consistent values is general practice and was not tested with several environments on this platform.</p></aside>
  </section>

  <section aria-labelledby="propagation">
    <h2 id="propagation">One trace identifier across HTTP and gRPC</h2>
    <p>A trace is the set of spans that share one 32-character <dfn>trace id</dfn>. The web service receives the HTTP request and starts the server span. When it calls the backend, it starts a client span and writes the trace context into the call. The backend reads the context and starts its own server span as a child of that client span. The standard that defines the header is W3C Trace Context, and its header is <code>traceparent</code>. For HTTP it is a request header, and for gRPC it travels as metadata with the call.</p>
    <figure>
      <pre class="mermaid">
sequenceDiagram
  autonumber
  participant C as Client
  participant W as Web service
  participant B as Backend
  C->>W: GET /chain
  W->>B: gRPC Process, traceparent in metadata
  B-->>W: reply
  W-->>C: 200 and x-trace-id
      </pre>
      <figcaption>The web service forwards the context at step 2. The backend server span reports the web client span as its parent, and both carry the trace id that the client reads from <code>x-trace-id</code> at step 4.</figcaption>
    </figure>
    <p>A <code>traceparent</code> value has four parts separated by hyphens: a version, the trace id, the id of the span making the call, and flags. Built from the ids in the captured trace below, the value the web service sends to the backend has this form (an illustration assembled from the sample, not a captured header):</p>
    <pre class="log">traceparent: 00-{tid}-{web_client["SpanId"]}-01</pre>
    <p>The captured data confirms the link. The backend server span has <code>ParentSpanId</code> <code>{backend_server["ParentSpanId"]}</code>, which is the <code>SpanId</code> of the web client span.</p>
    <aside class="caveat"><p><strong>What breaks a trace.</strong> If a service calls another service and does not forward <code>traceparent</code>, the called service starts a new trace. Its spans get a different trace id and have no parent, so a search for the first service's trace id finds only the first service's spans. This is how the standard behaves; the test app forwards the context in both services, so the course data has no such broken trace. When a request seems to stop at a service boundary in the waterfall, check that hop first.</p></aside>
  </section>

  <section aria-labelledby="logs">
    <h2 id="logs">Log lines that carry the ids</h2>
    <p>The test app's log records include the trace id and the span id of the span that was active when the line was written. In the captured trace below, the web service's line <em>chain handled</em> carries the id of the web server span, and the backend's line <em>backend processed</em> carries the id of the backend server span. A search for the trace id returns both lines, and the span id says which operation wrote each.</p>
    <p>The response header is the entry point. Every response except <code>/health</code> and <code>/admin/flush</code> has <code>x-trace-id</code>, the 32-character trace id of the server span. A request also carries <code>x-test-run-id</code>, which the app records as the attribute <code>test_run_id</code> on spans, log records and metrics, and forwards to the backend in gRPC metadata. Module 5 uses that attribute to follow a whole test run.</p>
  </section>

  <section aria-labelledby="explorer">
    <h2 id="explorer">Trace explorer</h2>
    <p>The waterfall below shows the four spans of one real <code>GET /chain</code> request. Select a span, with a click or by pressing Enter, to see its service, kind, status, ids and the log lines that carry its span id. The arrow keys move between spans.</p>
    <aside class="note"><p><strong>Bar lengths are illustrative.</strong> The four durations are all about 6 milliseconds, so drawn to scale the bars would look identical. The bars therefore show order and nesting only, each child indented under its parent. The real duration of each span is printed in text next to it and in the table below.</p></aside>
    <p class="controls-row">
      <button type="button" class="btn" data-trace-mode="chain" aria-pressed="true">Captured trace: GET /chain</button>
      <button type="button" class="btn" data-trace-mode="fail" aria-pressed="false">Contract shape: GET /chain-fail</button>
    </p>
    <div class="sim" id="trace-sim" data-trace="{esc(data_json)}">
      <figure>
        <svg id="trace-svg" viewBox="0 0 760 210" role="img" aria-labelledby="trace-t trace-d"><title id="trace-t">Waterfall of the spans of one request</title><desc id="trace-d">Each span is a row. Children are indented under their parents. Select a row to see its details below.</desc></svg>
        <figcaption id="trace-src">Captured trace of one GET /chain request.</figcaption>
      </figure>
      <div class="out trace-out" id="trace-out" aria-live="polite"><p>Select a span. Without JavaScript, the tables below list the same data.</p></div>
    </div>
    <p>The <code>/chain-fail</code> view is described from the per-request contract and is not a captured trace. A failing request has three spans, all with status Error, because the backend fails and the error passes up through the web client and server spans, and two ERROR log lines, one from each service. The web response is HTTP 502.</p>
    <div class="evid">
      <h3>The captured trace as data</h3>
      <p>Trace id <code>{tid}</code>.</p>
      <div class="trace-scroll"><table class="data">
        <thead><tr><th scope="col">Service</th><th scope="col">Kind</th><th scope="col">Name</th><th scope="col">Status</th><th scope="col" class="num">Duration (ms)</th><th scope="col">Span id</th><th scope="col">Parent span id</th></tr></thead>
        <tbody>
{span_rows}
        </tbody>
      </table></div>
      <div class="trace-scroll"><table class="data">
        <thead><tr><th scope="col">Service</th><th scope="col">Severity</th><th scope="col">Body</th><th scope="col">Span id on the log</th><th scope="col">Written inside</th></tr></thead>
        <tbody>
{log_rows}
        </tbody>
      </table></div>
      <p class="src">Source: {esc(man["source"])}, <code>{esc(man["command"])}</code>, captured {man["captured_at"]}. One request on one laptop; the durations show what that request took and are not a performance claim.</p>
    </div>
  </section>

  <section aria-labelledby="functions">
    <h2 id="functions">Function-level detail: how much code you write depends on the runtime</h2>
    <p>The spans so far (a request, a call to another service) come from one layer that wraps the whole request. Seeing the functions inside a service is a separate question, and the answer depends on the language.</p>
    <table class="data">
      <thead><tr><th scope="col">Runtime</th><th scope="col">Without changing code</th><th scope="col">Function-level spans</th></tr></thead>
      <tbody>
        <tr><td>.NET, Java, Python, Node.js</td><td>OpenTelemetry's zero-code instrumentation hooks the web framework, HTTP and database clients, and gRPC when you add a library to the container and set environment variables.</td><td>Your own methods usually need a line of code, or a configuration list of method names where the tool supports one.</td></tr>
        <tr><td>Rust (this test app)</td><td>No library hooks into compiled code to create spans, so spans are created in the source. An external eBPF-based tool could add request-level spans, not function spans, and was not tried here.</td><td>The <code>#[tracing::instrument]</code> attribute on a function, or one <code>#[trace_all]</code> on a block of them.</td></tr>
      </tbody>
    </table>
    <aside class="caveat"><p><strong>Not run here.</strong> The first row is how those tools are documented to work and was not run against this stack. Only the Rust row was built and tested.</p></aside>
    <h3>One attribute for a whole block</h3>
    <p><code>#[trace_all]</code> is a small macro in the test app (the <code>trace_macros</code> crate). Placed on an <code>impl</code> block or an inline module, it adds <code>#[tracing::instrument(skip_all)]</code> to every function inside, so each function becomes a span. <code>skip_all</code> keeps arguments out of the spans. A function that already has its own <code>#[instrument]</code>, or is named in <code>skip(...)</code>, is left alone.</p>
    <pre class="log">#[trace_all(skip(new))]
impl CheckoutService {{
    pub fn new(/* ... */) -&gt; Self {{ /* skipped: not traced */ }}

    pub async fn place_order(&amp;self) -&gt; Result&lt;(), tonic::Status&gt; {{ /* ... */ }}
    async fn validate_cart(&amp;self) {{ /* ... */ }}

    #[tracing::instrument(skip_all, fields(items = tracing::field::Empty, slowest_line_ms = tracing::field::Empty))]
    async fn price_items(&amp;self) {{ /* has its own attribute, so it is left alone */ }}
}}</pre>
    <p>It cannot cover a whole crate with one line, because a Rust macro only sees the code it is attached to. It is also not applied to every function by default: a span for each small helper raises the span volume and the storage cost, and buries the few spans that matter.</p>
    <h3>The captured result: {co_count} spans in one request</h3>
    <p>The fictitious <code>/checkout</code> flow has functions for validating the cart, pricing, discounts, stock, fraud checks, payment and a ledger, in both the web service and the backend. This is one real request, read from ClickHouse.</p>
    <div class="evid">
      <pre class="log">{co_tree}</pre>
      <p class="src">Source: {esc(co_man["source"])}, <code>{esc(co_man["command"])}</code>, captured {co_man["captured_at"]}. One request on one laptop; the durations are not a performance claim.</p>
    </div>
    <figure class="shot">
      <img src="../evidence/screens/trace-waterfall-checkout.png" alt="HyperDX trace view of one checkout request: a waterfall with web and backend function spans nested under the request">
      <figcaption>The same request in HyperDX. Bars nest by parent, and the function spans fall inside the span that called them.</figcaption>
    </figure>
    <h3>A loop is one span, not one span per pass</h3>
    <p><code>price_items</code> loops over five cart lines. A span per line would make 1,000 spans for a 1,000-line cart. Instead the function has one span, which covers the loop and the two pricing calls after it, and it records how many passes ran and how long the slowest took: <code>items={esc(str(co_price["items"]))}</code>, <code>slowest_line_ms={esc(str(co_price["slowest_line_ms"]))}</code> in the request above. Those two fields can be searched and charted like any attribute. A function that records fields declares them in its own <code>#[instrument]</code>, which the macro leaves alone. A metric histogram of pass times would add the usual spread, and this app does not have one.</p>
    <aside class="note"><p><strong>Cost.</strong> A <code>/checkout</code> request produces {co_count} spans, against 4 for <code>/chain</code>. Function-level detail multiplies span volume. The cost model in module 11 has no trace volume in it, so spans like these are not costed there.</p></aside>
  </section>

  <section aria-labelledby="contract">
    <h2 id="contract">What each request emits</h2>
    <p>The test app emits an exact, documented amount of telemetry for each endpoint, so a check can compare what arrived with what was sent. The table is the app's contract, from its README.</p>
    <table class="data">
      <thead><tr><th scope="col">Request</th><th scope="col">HTTP status</th><th scope="col">Spans (one trace)</th><th scope="col">Log records</th></tr></thead>
      <tbody>
{contract_rows}
      </tbody>
    </table>
  </section>

  <section aria-labelledby="config">
    <h2 id="config">Configuration: the exporter and the service name</h2>
    <p>The test app reads three environment variables for telemetry, as listed in its README.</p>
    <table class="data">
      <thead><tr><th scope="col">Variable</th><th scope="col">Default</th><th scope="col">Meaning</th></tr></thead>
      <tbody>
        <tr><td><code>OTEL_EXPORTER_OTLP_ENDPOINT</code></td><td>unset</td><td>The OTLP/gRPC endpoint to send to. When it is unset, no exporter is created and nothing goes over the network, although spans still get real ids.</td></tr>
        <tr><td><code>SERVICE_NAME</code></td><td><code>web</code> or <code>backend</code></td><td>The value of the <code>service.name</code> resource attribute.</td></tr>
        <tr><td><code>OTEL_METRIC_EXPORT_INTERVAL_MS</code></td><td><code>5000</code></td><td>How often metrics are exported, in milliseconds.</td></tr>
      </tbody>
    </table>
    <p>Example, from the README, for the backend service pointed at a local collector:</p>
    <pre class="log">SERVICE_NAME=backend BACKEND_ADDR=0.0.0.0:50051 \\
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 cargo run --release -p backend</pre>
    <p>The README documents this variable as an OTLP/gRPC endpoint. The collector's own configuration is covered in module 8.</p>
  </section>'''

q = quiz([
    {"q": "A new service sits between web and backend. It receives the HTTP request with a <code>traceparent</code> header but calls the backend without forwarding it. You take the <code>x-trace-id</code> from a response and search for that trace. What do you find?",
     "options": [
        ("All the spans of both services, because the backend infers the trace from the time of the call.", "The backend has nothing to infer the trace from except the header, so revisit what the called service does when no context arrives."),
        ("Nothing, because a missing header makes the collector drop the whole request for that trace.", "The collector does not drop spans for a missing header, and the earlier spans still carry the original id. Revisit where the context is read."),
        ("The spans up to the new service, while the backend's spans form a separate trace with no parent.", "The backend starts a new trace when no context arrives, so its spans carry another trace id."),
     ], "answer": 2},
    {"q": "A request passes through three services. Two of them were deployed with <code>deployment.environment.name=production</code> and the third was copied from a test setup and reports <code>staging</code>. You filter on <code>production</code> and look at the spans. What do you see for that request?",
     "options": [
        ("All spans of the request, because spans that share a trace id are always returned together whatever the filter says.", "The trace id links the spans, but a filter on a resource attribute is applied to every row separately, so revisit what the filter reads."),
        ("The spans of two services, so the request looks shorter than it was.", "The filter is applied to the attribute of each row."),
        ("All spans, with the third service's spans flagged as another environment, so the mismatch is easy to notice.", "A filter hides non-matching rows and does not flag them, so revisit how a filter on a resource attribute behaves."),
     ], "answer": 1},
    {"q": "An ERROR log line shows a trace id and the span id <code>cb2f60ca0374b58e</code> from a different request than the one you are looking at. How do you find where it was written?",
     "options": [
        ("Search spans by the line's trace id, then pick out that span id.", "The trace id selects the request and the span id picks the operation inside it."),
        ("Search spans by the span id alone, which gives the request's other spans.", "A span id names one span and does not select the other spans of the request, so revisit which id groups a request."),
        ("Search the logs again with the span id to find the response header.", "The header is returned to the client and is not stored on log lines, so revisit what the logs carry."),
     ], "answer": 0},
], gate="All correct. Continue to module 5 to read the dashboards and the error list built from this telemetry.")

prev, nxt = neighbours("m04")
write("04-sending-telemetry.html", page(
    "04-sending-telemetry.html", "m04", "Sending telemetry", "Using it", "1 of 3",
    "How an application identifies itself and passes one trace identifier from an HTTP request through a gRPC call into its log lines, shown on a real captured request.",
    ["set the resource attributes that identify a service and its environment,",
     "explain how a trace identifier travels from an HTTP request through a gRPC call and into log lines, and",
     "read a request's spans and logs together."],
    body, q, scripts=["sim-trace.js"], mermaid=True, prev=prev, next=nxt))
