#!/usr/bin/env python3
import re
from lib import page, quiz, write, neighbours, evidence, esc

ps = evidence("compose-ps.txt").strip().splitlines()[1:]
ps_rows = {}
for ln in ps:
    parts = re.split(r"\s{2,}", ln.strip())
    ps_rows[parts[0]] = (parts[1], parts[2])
counts = evidence("clickhouse-counts.json")
ddl = evidence("ddl.sql").splitlines()
man = evidence("manifest.json")

PURPOSE = {
    "otel-collector": ("Collector", "Receives OTLP on 4317 (gRPC) and 4318 (HTTP), batches, and writes each signal to the Buffer. Built from <code>collector/</code>: the OpenData exporter v0.5.0 with two local patches."),
    "s3": ("Object store (RustFS)", "Local S3-compatible storage that holds the Buffer. Ports 9000 (S3 API) and 9001. It must support S3 conditional writes for the manifest."),
    "s3-init": ("Bucket initialiser", "Creates the bucket <code>observability-buffer</code> and exits. It shows as <code>Exited (0)</code> when it succeeded."),
    "ingestor-logs": ("Ingestor, logs", "Reads <code>otel/logs/manifest</code> and inserts into <code>otel_logs</code>. Metrics on port 9091."),
    "ingestor-traces": ("Ingestor, traces", "Reads <code>otel/traces/manifest</code> and inserts into <code>otel_traces</code>. Metrics on port 9092."),
    "ingestor-metrics": ("Ingestor, metrics", "Reads <code>otel/metrics/manifest</code> and inserts into the three metric tables. Metrics on port 9093."),
    "clickhouse": ("ClickHouse", "The database. HTTP on port 8123. Tables are created from <code>stack/clickhouse/*.sql</code> at first start."),
    "hyperdx": ("HyperDX", "Search, dashboards and alerts. UI on port 8080, external API on port 8000."),
    "mongo": ("MongoDB", "HyperDX's own application database, for its users, connections, dashboards and alert definitions. It does not hold telemetry."),
    "web": ("Test app, web", "The axum web service on port 8081 that generates the test telemetry. Same image as the backend."),
    "backend": ("Test app, backend", "The tonic gRPC service on port 50051, called by the web service."),
    "webhook-sink": ("Webhook sink", "Records the notifications HyperDX posts when an alert fires or resolves (port 8089, <code>GET /received</code>)."),
}
ORDER = ["otel-collector", "s3", "s3-init", "ingestor-logs", "ingestor-traces", "ingestor-metrics", "clickhouse", "hyperdx", "mongo", "web", "backend", "webhook-sink"]
comp_rows = "\n".join(
    f'        <tr><td><code>{k}</code><br><span class="tag">{esc(PURPOSE[k][0])}</span></td><td><code>{esc(ps_rows[k][0])}</code></td><td>{PURPOSE[k][1]}</td></tr>'
    for k in ORDER)
ps_pre = esc(evidence("compose-ps.txt").rstrip())

# real DDL excerpt for the logs table, taken verbatim by line from evidence/ddl.sql
start = next(i for i, l in enumerate(ddl) if l.startswith("CREATE TABLE IF NOT EXISTS otel.otel_logs"))
end = next(i for i in range(start, len(ddl)) if ddl[i].startswith("SETTINGS"))
block = ddl[start:end + 1]
def pick(prefix):
    return next(l for l in block if l.strip().startswith(prefix))
excerpt = [block[0], block[1], block[2], block[3], "    -- ... 14 more columns omitted ..."]
excerpt += [l for l in block if "_odb_" in l.split()[0] or l.strip().startswith("_adapter_version") or "_odb_clickhouse_inserted_at" in l]
excerpt += ["    -- ... 3 skip indexes omitted ..."]
excerpt += [l for l in block if l.startswith(")") or l.startswith("ENGINE") or l.startswith("PARTITION") or l.startswith("ORDER") or l.startswith("TTL") or l.startswith("SETTINGS")]
ddl_pre = esc("\n".join(excerpt))
assert "ReplacingMergeTree" in ddl_pre and "non_replicated_deduplication_window = 1000" in ddl_pre

body = f'''  <section aria-labelledby="components">
    <p>This module names every component of the platform, shows how each is configured, and walks through starting the stack and finding where a signal lives. The stack is the learning and trial environment: it runs on one machine under Docker or Podman Compose, and it is not a production deployment.</p>
    <aside class="caveat"><p><strong>Learning and trial environment.</strong> The credentials on this page are the documented development defaults and are for local use only. Nothing here has been run on AWS, and module 11 lists what that would involve.</p></aside>
    <h2 id="components">Components</h2>
    <p>The stack has twelve services in <code>stack/compose.yml</code>. The Buffer is not a service of its own: it is the bucket <code>observability-buffer</code> on the object store. Images and status come from a real <code>docker compose -p obs ps -a</code> captured on {man["compose-ps.txt"]["captured_at"][:10]}.</p>
    <table class="data">
      <thead><tr><th scope="col">Service</th><th scope="col">Image</th><th scope="col">Purpose</th></tr></thead>
      <tbody>
{comp_rows}
      </tbody>
    </table>
    <p>Images tagged <code>localhost/sre-*:dev</code> are built from this repository: the test app from <code>app/</code>, the collector from <code>collector/</code> and the ingestors from <code>ingest/</code>. All other images are pulled. The local object store is RustFS because the MinIO images were removed from the registry in 2026.</p>
  </section>

  <section aria-labelledby="signals">
    <h2 id="signals">Per-signal manifests and tables</h2>
    <p>Each signal has its own pipeline in the collector, manifest in the Buffer, ingestor process and set of tables. The tables follow the shape of HyperDX's default (ClickStack) schema and add provenance columns.</p>
    <table class="data">
      <thead><tr><th scope="col">Signal</th><th scope="col">Manifest</th><th scope="col">Data prefix</th><th scope="col">Ingestor</th><th scope="col">Tables</th></tr></thead>
      <tbody>
        <tr><td>Logs</td><td><code>otel/logs/manifest</code></td><td><code>otel/logs/data</code></td><td><code>ingestor-logs</code></td><td><code>otel_logs</code></td></tr>
        <tr><td>Traces</td><td><code>otel/traces/manifest</code></td><td><code>otel/traces/data</code></td><td><code>ingestor-traces</code></td><td><code>otel_traces</code></td></tr>
        <tr><td>Metrics</td><td><code>otel/metrics/manifest</code></td><td><code>otel/metrics/data</code></td><td><code>ingestor-metrics</code></td><td><code>otel_metrics_gauge</code>, <code>otel_metrics_sum</code>, <code>otel_metrics_histogram</code></td></tr>
      </tbody>
    </table>
  </section>

  <section aria-labelledby="config">
    <h2 id="config">Configuration</h2>
    <p>Everything is a container configured by YAML and environment variables, so the same images and files move to another environment by changing the environment.</p>
    <h3>Collector</h3>
    <p><code>stack/config/otel-collector.yaml</code> defines one exporter per signal, <code>opendata/logs</code>, <code>opendata/traces</code> and <code>opendata/metrics</code>, which differ only in their paths. Environment-specific values are read from the environment:</p>
<pre><code>object_store:
  type: s3
  bucket: ${{env:BUFFER_BUCKET}}
  region: ${{env:AWS_REGION}}
  endpoint: ${{env:S3_ENDPOINT:-}}</code></pre>
    <p>Locally, <code>compose.yml</code> sets <code>S3_ENDPOINT</code> to the RustFS service. On AWS the variable is left empty, the endpoint falls back to the real S3, and credentials come from the task or pod IAM role. The other exporter settings in the file are <code>flush_interval: 100ms</code>, <code>flush_size_bytes: 4194304</code>, <code>compression: zstd</code> and <code>timeout: 30s</code>. The 100 ms flush is tuned for fast results locally, and module 11 shows its cost on AWS.</p>
    <h3>Ingestors</h3>
    <p>Each ingestor reads <code>stack/config/ingestor-&lt;signal&gt;.yaml</code>. The three files differ in <code>signal</code>, <code>manifest_path</code> and <code>data_prefix</code>. Selected settings:</p>
    <table class="data">
      <thead><tr><th scope="col">Setting</th><th scope="col">Value</th><th scope="col">Effect</th></tr></thead>
      <tbody>
        <tr><td><code>runtime.poll_interval_ms</code></td><td>250</td><td>How often the ingestor checks the manifest.</td></tr>
        <tr><td><code>runtime.retry_max_attempts</code></td><td>6</td><td>Retries with backoff before an error is surfaced.</td></tr>
        <tr><td><code>ack.policy</code></td><td><code>every_commit_group</code></td><td>Acknowledge a range after each committed group.</td></tr>
        <tr><td><code>adapter.max_chunk_rows</code> and <code>max_chunk_bytes</code></td><td>10000 and 4194304</td><td>Upper bounds for one insert, and inputs to the token fingerprint.</td></tr>
        <tr><td><code>adapter.apply_deduplication_token</code></td><td><code>true</code></td><td>Send the idempotency token with every insert.</td></tr>
      </tbody>
    </table>
    <p>Any setting can be overridden by an <code>INGESTOR__*</code> variable, with a double underscore for each level of nesting. The files name <code>INGESTOR__CLICKHOUSE__ENDPOINT</code>, <code>INGESTOR__CLICKHOUSE__USER</code>, <code>INGESTOR__CLICKHOUSE__PASSWORD</code> and <code>INGESTOR__BUFFER__OBJECT_STORE__BUCKET</code>. <code>INGESTOR__BUFFER__OBJECT_STORE__REGION</code> overrides the region written in the YAML. Object-store credentials and the endpoint come from the standard <code>AWS_*</code> variables.</p>
  </section>

  <section aria-labelledby="ddl">
    <h2 id="ddl">The logs table</h2>
    <p>This excerpt is taken line by line from <code>evidence/ddl.sql</code>, which is a copy of <code>stack/clickhouse/10_logs.sql</code>. The columns in the middle are omitted.</p>
<pre class="log"><code>{ddl_pre}</code></pre>
    <table class="data">
      <thead><tr><th scope="col">Line</th><th scope="col">What it does</th></tr></thead>
      <tbody>
        <tr><td><code>_odb_sequence</code>, <code>_odb_entry_index</code>, <code>_odb_record_index</code></td><td>Provenance: which queue sequence, which entry in it and which record in that entry the row came from. Together they identify a record.</td></tr>
        <tr><td><code>_odb_manifest_path</code>, <code>_odb_data_path</code>, <code>_odb_ingestion_time_ms</code></td><td>Provenance: the manifest and the data object the row was read from, and when the ingestor read it.</td></tr>
        <tr><td><code>_adapter_version</code></td><td>The adapter version, and the version column that <code>ReplacingMergeTree</code> uses to choose which of two rows to keep.</td></tr>
        <tr><td><code>_odb_clickhouse_inserted_at DEFAULT now64(9)</code></td><td>The server's own insert time. End-to-end latency is this value minus the event time.</td></tr>
        <tr><td><code>ENGINE = ReplacingMergeTree(_adapter_version)</code></td><td>Rows with the same sort key collapse on merge.</td></tr>
        <tr><td><code>PARTITION BY toDate(Timestamp)</code></td><td>One partition per day, so expiry drops whole days.</td></tr>
        <tr><td><code>ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp, _odb_sequence, ...)</code></td><td>The sort key. The time bucket and service come first so time-bounded searches skip data. The three provenance columns at the end make the key unique per record, which is what lets duplicates collapse. Dropping them would let distinct records with the same time and service collapse into one.</td></tr>
        <tr><td><code>TTL toDateTime(Timestamp) + INTERVAL 30 DAY</code></td><td>Rows expire 30 days after their event time. With <code>ttl_only_drop_parts = 1</code> ClickHouse removes whole parts only.</td></tr>
        <tr><td><code>non_replicated_deduplication_window = 1000</code></td><td>ClickHouse remembers the tokens of the last 1,000 inserts and drops a repeat. This is what makes the insert token work on a single node.</td></tr>
      </tbody>
    </table>
    <p>The traces table has the same provenance and settings, with the sort key <code>(ServiceName, SpanName, toDateTime(Timestamp), _odb_sequence, _odb_entry_index, _odb_record_index)</code>. A read-only user <code>hyperdx_ro</code> with <code>SELECT</code> on the <code>otel</code> database is what HyperDX uses.</p>
  </section>

  <section aria-labelledby="setup">
    <h2 id="setup">Setup checklist</h2>
    <p>Run the commands from the <code>stack/</code> directory. Tick each step when its expected result appears. Progress is stored in your browser only. Outputs marked as examples come from a real run, and the numbers and times will differ on your machine.</p>
    <div class="sim" id="chk-panel">
      <p class="controls-row"><span class="readout" id="chk-progress" aria-live="polite">0 of 9 steps done</span> <button type="button" class="btn" id="chk-reset">Reset</button></p>
      <ol class="chk-list">
        <li class="chk-step"><label><input type="checkbox" data-step="build"> <strong>Build the three images once.</strong></label>
<pre><code>docker build -t sre-app:dev                       ../app
docker build -f ../collector/otel-collector/Dockerfile -t sre-collector:dev ../collector
docker build -f ../ingest/connectors/clickhouse-ingestor/Dockerfile -t sre-ingestor:dev ../ingest</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> Each build finishes without an error and tags <code>sre-app:dev</code>, <code>sre-collector:dev</code> and <code>sre-ingestor:dev</code>. The first build takes a while.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="up"> <strong>Start the stack and the test app.</strong></label>
<pre><code>docker compose --profile app up -d</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> The command returns and the containers start. <code>s3-init</code> runs once and exits.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="ps"> <strong>Confirm every service is up.</strong></label>
<pre><code>docker compose -p obs ps -a</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> Every service is <code>Up</code> except <code>s3-init</code>, which shows <code>Exited (0)</code>. Example output from a real run, with times that differ on your machine:</p>
<pre class="log"><code>{ps_pre}</code></pre></li>
        <li class="chk-step"><label><input type="checkbox" data-step="provision"> <strong>Create the HyperDX connection, sources, dashboards and saved searches.</strong></label>
<pre><code>python3 hyperdx/provision.py</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> The script exits with status 0, and it is safe to run again because it is idempotent. HyperDX then has the dashboards <em>Availability &amp; Latency</em> and <em>Runlog</em>.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="verify"> <strong>Prove the pipeline with a load run and the checks.</strong></label>
<pre><code>python3 verify/verify.py</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> The exit status is 0 and all 32 checks pass. Module 9 describes them.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="counts"> <strong>Look at the rows in ClickHouse.</strong> The user and password are the documented development defaults.</label>
<pre><code>curl -s -u ingestor:ingestor http://localhost:8123/ \\
  --data-binary "SELECT count() FROM otel.otel_logs"</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> A number greater than zero. Example row counts from a real stack after several verification runs: <code>otel_logs</code> {counts["otel_logs"]}, <code>otel_traces</code> {counts["otel_traces"]}, <code>otel_metrics_sum</code> {counts["otel_metrics_sum"]}, <code>otel_metrics_histogram</code> {counts["otel_metrics_histogram"]}. Your numbers depend on how much traffic you sent.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="metrics"> <strong>Check an ingestor's own metrics.</strong></label>
<pre><code>curl -s localhost:9091/metrics</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> Prometheus-format text from the logs ingestor. Ports 9092 and 9093 serve the traces and metrics ingestors.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="ui"> <strong>Open HyperDX.</strong></label>
          <p>Go to <code>http://localhost:8080</code> and sign in with the development login <code>admin@local.dev</code> and <code>LocalDevPass1!</code> (local use only).</p>
          <p class="chk-expect"><strong>Expected.</strong> The Logs, Traces and Metrics sources show data, and the dashboards from step 4 are listed.</p></li>
        <li class="chk-step"><label><input type="checkbox" data-step="more"> <strong>Optional: run the negative, failure and alert tests.</strong></label>
<pre><code>python3 verify/selftest.py
python3 verify/chaos.py
python3 verify/alerts.py</code></pre>
          <p class="chk-expect"><strong>Expected.</strong> Each exits with status 0. <code>alerts.py</code> takes about 6 to 25 minutes because HyperDX evaluates alerts once a minute.</p></li>
      </ol>
    </div>
  </section>

  <section aria-labelledby="where">
    <h2 id="where">Where does my data live?</h2>
    <p>Choose a signal and a stage to see the location and a way to look at it.</p>
    <div class="sim" id="loc-panel">
      <div class="controls">
        <label class="ctl">Signal
          <select id="loc-signal"><option value="logs">Logs</option><option value="traces">Traces</option><option value="metrics">Metrics</option></select></label>
        <label class="ctl">Stage
          <select id="loc-stage"><option value="collector">In the collector</option><option value="buffer" selected>In the Buffer on S3</option><option value="ingestor">In the ingestor</option><option value="clickhouse">In ClickHouse</option><option value="hyperdx">In HyperDX</option></select></label>
      </div>
      <p class="out" aria-live="polite"><strong id="loc-where">Logs in the Buffer: manifest <code>otel/logs/manifest</code>.</strong></p>
      <p id="loc-detail">Choose a stage. This text is filled in by the page script.</p>
      <p class="readout" id="loc-query"></p>
    </div>
  </section>

  <section aria-labelledby="aws">
    <h2 id="aws">Moving to AWS</h2>
    <p>The README maps the local stack to AWS by changing configuration: an S3 bucket instead of RustFS, the collector and ingestors as container deployments, ClickHouse on ClickHouse Cloud or EC2 or EKS, and secrets from a secrets service instead of the development defaults. This has been documented and not executed. Module 11 has the full mapping and the open limits.</p>
  </section>'''

q = quiz([
    {"q": "A team deploys the same collector image and YAML file to AWS. Which change lets it write to a real S3 bucket?",
     "options": [
        ("Leave S3_ENDPOINT empty, set BUFFER_BUCKET and AWS_REGION, and use the IAM role for credentials.", "The configuration reads the endpoint from the environment, so the same file serves both environments once the variables differ."),
        ("Rebuild the image with the exporter type changed from s3 to aws and a new build for each region.", "The exporter type is already s3, and the environment-specific values are variables. Revisit what the configuration reads from the environment."),
        ("Copy the local development credentials into the YAML file so the bucket accepts the writes.", "The development credentials belong to the local object store only, and credentials come from the environment or the role. Revisit the dev-defaults note."),
     ], "answer": 0},
    {"q": "Someone recreates otel_logs with an ORDER BY that has only the time bucket, ServiceName and Timestamp, without the _odb_* columns. What is the risk?",
     "options": [
        ("The insert token stops working, so every replay of a chunk doubles the rows in the table.", "The token is checked against the table's recent inserts and does not depend on the sort key. Revisit the two defences against duplicates."),
        ("Two different records from one service with the same timestamp could be treated as duplicates and merged.", "The sort key is the identity that ReplacingMergeTree uses, so records without a distinguishing column can be collapsed."),
        ("The 30-day expiry no longer runs, because the TTL depends on the sort key, so the table grows without limit.", "Expiry is set by the TTL clause and the partition key and is separate from the sort key. Revisit what each clause of the DDL controls."),
     ], "answer": 1},
    {"q": "Logs appear in HyperDX but no new traces do. Which first check follows from the architecture?",
     "options": [
        ("Check ingestor-logs and the otel_logs table, because all signals share one pipeline and one ingestor.", "Each signal has its own pipeline, manifest and ingestor, so the logs path cannot explain missing traces. Revisit the per-signal table."),
        ("Restart HyperDX, because it stores the trace data and may have stopped reading new traces.", "HyperDX only reads ClickHouse and is not in the data path, and its own database holds configuration. Revisit the component table."),
        ("Look at the traces path: its manifest, ingestor-traces on port 9092 and the otel_traces table.", "Traces have their own queue, ingestor and table, so those are the places the data can stop."),
     ], "answer": 2},
], gate="All correct. Continue to module 9, how we know it works.")

prev, nxt = neighbours("m08")
write("08-architecture-and-setup.html", page(
    "08-architecture-and-setup.html", "m08", "Architecture and setup", "Running it", "2 of 5",
    "Every component of the platform, how each is configured, and how to start the stack and confirm each stage works.",
    ["name each component and its configuration,",
     "start the stack and confirm each stage works, and",
     "find where each signal lives at each stage."],
    body, q, scripts=["sim-checklist.js"], mermaid=False, prev=prev, next=nxt))
