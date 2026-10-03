#!/usr/bin/env python3
from lib import page, quiz, write, neighbours

def node(key, x, y, label, title):
    return (f'<g data-node="{key}" tabindex="0" role="button" aria-pressed="false" aria-label="{title}">'
            f'<rect class="d-node" x="{x}" y="{y}" width="125" height="56" rx="4"/>'
            f'<text class="d-text" x="{x + 62}" y="{y + 33}">{label}</text></g>')

svg = '''<svg viewBox="0 0 800 280" role="img" aria-labelledby="arch-t arch-d">
<title id="arch-t">Architecture of the platform</title>
<desc id="arch-d">Applications send OTLP to the collector, which writes to the Buffer on S3. Ingestors read the Buffer and insert into ClickHouse. HyperDX reads ClickHouse and posts alerts to a webhook.</desc>
<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path class="d-arrowhead" d="M0 0 L10 5 L0 10 z"/></marker></defs>
<path class="d-edge" d="M135 68 L168 68" marker-end="url(#arr)"/>
<path class="d-edge" d="M295 68 L328 68" marker-end="url(#arr)"/>
<path class="d-edge" d="M455 68 L488 68" marker-end="url(#arr)"/>
<path class="d-edge" d="M615 68 L648 68" marker-end="url(#arr)"/>
<path class="d-edge" d="M712 200 L712 126" marker-end="url(#arr)"/>
<path class="d-edge" d="M648 228 L618 228" marker-end="url(#arr)"/>
''' + "\n".join([
    node("apps", 10, 40, "Applications", "Applications: show details"),
    node("collector", 170, 40, "Collector", "Collector: show details"),
    node("buffer", 330, 40, "Buffer (S3)", "Buffer on S3: show details"),
    node("ingestors", 490, 40, "Ingestors x3", "Ingestors: show details"),
    node("clickhouse", 650, 40, "ClickHouse", "ClickHouse: show details"),
    node("hyperdx", 650, 200, "HyperDX", "HyperDX: show details"),
    node("webhook", 490, 200, "Alert webhook", "Alert webhook: show details"),
]) + "\n</svg>"


import json, re, os
from lib import evidence, esc, COURSE
PAY = os.path.join(COURSE, "evidence", "payloads")
def pay(name):
    path = os.path.join(PAY, name)
    txt = open(path).read()
    return json.loads(txt) if name.endswith(".json") else txt

def hop_otlp():
    raw = pay("01-otlp-decoded-by-debug-collector.txt").splitlines()
    head = raw[:2]
    # drop the collector's own log prefix (timestamp, level) and its one-line resource blob
    t = [re.sub(r"^\S+\s+info\s+", "", l) for l in raw[2:] if '{"resource"' not in l]
    try:
        i = next(k for k, l in enumerate(t) if l.startswith("ResourceSpans #"))
        j = next(k for k, l in enumerate(t) if k > i and l.startswith("Span #") and "Name           : GET /chain" in "\n".join(t[k:k + 6]))
        k2 = next((k for k in range(j + 1, len(t)) if t[k].startswith("Span #") or t[k].startswith("ResourceSpans") or t[k].startswith("ResourceLog")), len(t))
        body = t[i:i + 8] + ["    ..."] + t[j:k2]
    except StopIteration:
        body = t[:48]
    return "\n".join(head + [""] + body)

def hop_buffer():
    d = pay("02-buffer-manifest-and-batch.json"); e = d["entries"][0]
    spans = [sp for rs in e["otlp_payload_decoded"]["resourceSpans"] for ss in rs["scopeSpans"] for sp in ss["spans"]]
    one = next(sp for sp in spans if sp["name"] == "GET /chain")
    out = {"manifest": d["manifest"], "manifest_bytes": d["manifest_bytes"], "queued_entries": d["queued_entries"],
           "entry": {k: e[k] for k in ("batch_object", "batch_object_bytes", "metadata_bytes", "envelope", "payloads_in_batch", "otlp_payload_bytes")},
           "otlp_payload_decoded (one of %d spans shown)" % len(spans): one}
    return json.dumps(out, indent=2), e, len(spans)

def hop_insert():
    d = pay("03-clickhouse-insert.json")
    return json.dumps({k: d[k] for k in ("user", "query", "written_rows", "dedup_token")}, indent=2), d

def hop_row():
    rows = pay("04-stored-rows.json")["rows"]
    r = next(x for x in rows if x["SpanName"] == "GET /chain")
    return json.dumps(r, indent=2), r

def hop_sql():
    t = pay("05-hyperdx-queries.txt")
    blocks = [b for b in t.split("## ") if b.startswith("latency percentile")]
    b = blocks[0] if blocks else t
    head, _, sql = b.partition("\n")
    sql = re.sub(r"\s+", " ", sql).strip()
    sql = re.sub(r"(, | FROM | WHERE | GROUP BY )", lambda m: "\n  " + m.group(1).strip() + " " if m.group(1) in (" FROM ", " WHERE ", " GROUP BY ") else m.group(1), sql)
    return "## " + head + "\n" + sql

def hop_webhook():
    d = pay("06-alert-webhook.json")
    f = next(x for x in d if x.get("state") == "ALERT")
    return json.dumps({k: f[k] for k in ("title", "state", "body", "link") if k in f}, indent=2)

otlp_txt = hop_otlp(); buf_txt, buf_e, nspans = hop_buffer(); ins_txt, ins = hop_insert(); row_txt, row = hop_row(); sql_txt = hop_sql(); hook_txt = hop_webhook()
seq = row["_odb_sequence"]
tok = ins["dedup_token"]
TABS = [
 ("otlp", "1 App to collector"), ("buffer", "2 Collector to Buffer"), ("insert", "3 Ingestor to ClickHouse"),
 ("row", "4 Row as stored"), ("sql", "5 HyperDX to ClickHouse"), ("hook", "6 HyperDX to webhook")]
NOTES = {
 "otlp": "One of the web service's two spans for a <code>GET /chain</code> request, as OTLP. The wire format is protobuf over gRPC, so this is the same message decoded to text by a debug exporter. The client span <code>backend.Process</code> has the server span as its parent, and the resource attributes identify the service and environment.",
 "buffer": f"The collector wrote the batch as one object of {buf_e['batch_object_bytes']} bytes that holds a {buf_e['otlp_payload_bytes']}-byte OTLP payload with {nspans} spans, and listed it in the manifest, which is {pay('02-buffer-manifest-and-batch.json')['manifest_bytes']} bytes. The envelope bytes <code>{buf_e['metadata_bytes']}</code> (base64 for the four bytes 1, 3, 1, 0) mean version 1, signal 3 (traces), encoding 1 (OTLP protobuf), which is how an ingestor knows which decoder to use.",
 "insert": f"The ingestor inserts rows with this statement and sends the rows as JSON lines in the request body. The idempotency token is sent as a setting. Read it from the left: the manifest, the table, the Buffer sequence range <code>{tok.split(':')[2]}</code>, the adapter version, a fingerprint of the chunking settings, and the chunk number. A replay of the same range produces the same token.",
 "row": f"The same request as stored in ClickHouse, with the columns HyperDX reads first and the provenance columns last. <code>_odb_sequence</code> is {seq}, the same number as the sequence range in the token, so a stored row can be traced back to the Buffer batch it came from. <code>_odb_clickhouse_inserted_at</code> is set by ClickHouse when the row is inserted and is how latency is measured.",
 "sql": "One of the queries HyperDX sent while drawing the dashboard, read from ClickHouse's query log. It is plain SQL over the same tables, run as the read-only user. The heading line shows how many rows and bytes the query read.",
 "hook": "The notification HyperDX posts when an alert fires. The body carries the count and the evaluated time range, and the link opens the dashboard tile.",
}
panels = []
for key, label in TABS:
    txt = {"otlp": otlp_txt, "buffer": buf_txt, "insert": ins_txt, "row": row_txt, "sql": sql_txt, "hook": hook_txt}[key]
    panels.append(f'<div data-payload-panel="{key}" hidden><p>{NOTES[key]}</p><pre class="log"><code>{esc(txt)}</code></pre></div>')
tabs_html = '<p class="controls-row">' + " ".join(f'<button type="button" class="btn btn--tab" data-payload-tab="{k}" aria-pressed="false">{l}</button>' for k, l in TABS) + '</p>'
trace2 = pay("04-stored-rows.json")["trace_id"]; trace1 = pay("01-otlp-decoded-by-debug-collector.txt").splitlines()[0].split("Trace id ")[-1]
man = evidence("manifest.json")
payload_section = f'''  <section aria-labelledby="payloads">
    <h2 id="payloads">Follow one span through the platform</h2>
    <p>Each tab shows the real payload at one hop, captured from the running stack for a single <code>GET /chain</code> request. Hops 2 to 4 are one request through the real stack (trace <code>{trace2[:8]}…</code>) and tie together by the Buffer sequence {seq}. Hop 1 was captured from a throwaway copy of the services on a separate network (trace <code>{trace1[:8]}…</code>), because the real collector does not print what it receives. It is a different request of the same shape.</p>
    <div class="sim" id="payload-tour">
      <h3>Payload at each hop</h3>
      {tabs_html}
      {"".join(panels)}
      <p class="readout">Captured {man["payloads/02-buffer-manifest-and-batch.json"]["captured_at"]} by <code>course/tools/capture-payloads.py</code>. The Buffer step pauses the traces ingestor for a few seconds so the entry is still queued when it is read.</p>
    </div>
  </section>

'''

body = f'''  <section aria-labelledby="path">
    <h2 id="path">One request, end to end</h2>
    <p>Take a request to the web service at <code>/chain</code>. It calls the backend over gRPC and returns 200. Each service reports what it saw to the collector over OTLP. For this request that is four spans and two log lines in total: the web server span and its client span, then the backend server span and an internal span. The response carries the trace identifier in an <code>x-trace-id</code> header, so you can look the request up afterwards.</p>
    <figure>
      <pre class="mermaid">
sequenceDiagram
  autonumber
  participant C as Client
  participant W as Web service
  participant B as Backend
  C->>W: GET /chain
  W->>B: gRPC Process (trace context)
  B-->>W: reply
  W-->>C: 200 and x-trace-id
      </pre>
      <figcaption>The client has its answer after step 4. The services report their telemetry afterwards, so a slow or unavailable collector does not slow the request.</figcaption>
    </figure>
    <p>Each service then sends what it saw to the collector, which batches it and writes each batch to the Buffer, a durable queue on S3. The collector reports a batch as exported only after the Buffer write is durable.</p>
    <figure>
      <pre class="mermaid">
sequenceDiagram
  autonumber
  participant S as Web and backend
  participant X as Collector
  participant Q as Buffer on S3
  S->>X: OTLP, 2 spans and 1 log each
  X->>Q: write batch object, update manifest
  Q-->>X: durable
      </pre>
      <figcaption>Data is durable at step 3. Before that it exists only in the collector's memory.</figcaption>
    </figure>
    <p>An ingestor then reads the batch from the Buffer, inserts it into ClickHouse, and acknowledges the range afterwards. HyperDX queries ClickHouse separately, with a read-only user.</p>
    <figure>
      <pre class="mermaid">
sequenceDiagram
  autonumber
  participant Q as Buffer on S3
  participant I as Ingestor
  participant D as ClickHouse
  I->>Q: read manifest, fetch batch
  I->>D: insert chunk with token T
  D-->>I: accepted
  I->>Q: acknowledge range
      </pre>
      <figcaption>The data is visible in ClickHouse at step 3. A crash between steps 3 and 4 repeats steps 1 to 3 with the same token T, and ClickHouse drops the repeat.</figcaption>
    </figure>
  </section>

  <section aria-labelledby="picture">
    <h2 id="picture">The platform in one picture</h2>
    <p>Select a stage to see what it does and how it fails. Every stage has a stated failure behaviour, and module 9 shows the tests that check each one.</p>
    <figure>
      {svg}
      <figcaption>Data flows left to right. HyperDX reads ClickHouse and sends alert notifications to the webhook, and it is not in the ingestion path.</figcaption>
    </figure>
    <div class="sim" id="arch-panel">
      <h3 id="arch-name">Select a stage</h3>
      <p id="arch-does"></p>
      <p id="arch-fails"></p>
      <p class="readout" id="arch-evid"></p>
    </div>
  </section>

{payload_section}  <section aria-labelledby="signals">
    <h2 id="signals">Three signals, three queues</h2>
    <p>Logs, metrics and traces do not share a queue. Each signal has its own pipeline in the collector, its own manifest in the Buffer, its own ingestor process, and its own ClickHouse tables.</p>
    <table class="data">
      <thead><tr><th scope="col">Signal</th><th scope="col">Buffer manifest</th><th scope="col">ClickHouse tables</th></tr></thead>
      <tbody>
        <tr><td>Logs</td><td><code>otel/logs/manifest</code></td><td><code>otel_logs</code></td></tr>
        <tr><td>Traces</td><td><code>otel/traces/manifest</code></td><td><code>otel_traces</code></td></tr>
        <tr><td>Metrics</td><td><code>otel/metrics/manifest</code></td><td><code>otel_metrics_gauge</code>, <code>otel_metrics_sum</code>, <code>otel_metrics_histogram</code></td></tr>
      </tbody>
    </table>
    <aside class="note"><p><strong>Consequence.</strong> A problem with one signal, such as an ingestor that cannot decode an entry, stays in that signal's queue and does not stall the other two.</p></aside>
  </section>'''

q = quiz([
    {"q": "The traces ingestor is stopped for ten minutes while traffic continues. Where does the trace data wait, and what happens when the ingestor restarts?",
     "options": [
        ("It waits in the Buffer on S3; after the restart the ingestor reads the manifest and inserts it, and ClickHouse drops repeats.", "The queue is the durable store between the collector and the ingestors, so the stopped ingestor only delays delivery."),
        ("It waits in the collector's memory, which holds every batch until the ingestor is back and then sends it again.", "The collector reports a batch as exported once it is in the Buffer, so the waiting data is in the queue and not in collector memory. Revisit the data-path figure."),
        ("It is lost, because ClickHouse never received it and the collector has already discarded its copy of the batch.", "Data is written to the Buffer before the ingestor reads it, so an ingestor outage delays the data and does not delete it."),
     ], "answer": 0},
    {"q": "Why does each signal have its own Buffer queue and ingestor?",
     "options": [
        ("S3 allows only one manifest file per bucket, so each signal needs a bucket of its own to hold its queue.", "A bucket can hold any number of manifests; the separation is a design choice, so look at what it prevents."),
        ("A fault in one signal stays in its own queue, and the ingest runtime decodes one signal per manifest.", "Separating the queues contains problems such as a stalled consumer, and each ingestor is configured for the one decoder it needs."),
        ("ClickHouse can accept only one signal at a time, so the three ingestors must take turns inserting their data.", "ClickHouse accepts all three at once; the limit is not in the database."),
     ], "answer": 1},
    {"q": "Which statement about the collector's durability is accurate?",
     "options": [
        ("Everything it receives is durable immediately because the Buffer is on S3, so a crash loses nothing it has accepted.", "The Buffer write happens after batching, so data waiting in the collector is not yet in S3."),
        ("It never loses data, because the applications resend whatever it drops until it confirms the batch was exported.", "Applications retry only for a limited time, and a retry can also create duplicates, so the first hop cannot be assumed lossless."),
        ("It reports a batch exported only once the Buffer write is durable, but batches still in memory are lost if it crashes.", "Durability starts when the Buffer write succeeds, and anything before that point exists only in memory."),
     ], "answer": 2},
], gate="All correct. Continue to module 3, the evidence it works.")

prev, nxt = neighbours("m02")
write("02-platform-in-one-picture.html", page(
    "02-platform-in-one-picture.html", "m02", "The platform in one picture", "Story", "2 of 3",
    "How telemetry travels from an application to a dashboard, which stage holds it at each moment, and what each stage does when it fails.",
    ["trace one request from the application to a query in HyperDX,",
     "say where data is durable at each step, and",
     "predict where data waits when one stage is down."],
    body, q, scripts=["sim-architecture.js", "sim-payload.js"], mermaid=True, prev=prev, next=nxt))
