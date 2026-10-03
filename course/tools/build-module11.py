#!/usr/bin/env python3
import re
from lib import bill_history, page, quiz, write, neighbours, evidence, esc

cmp_ = evidence("comparison.json")
cost = evidence("cost-model-output.txt")
man = evidence("manifest.json")
alerts = evidence("alerts-report.json"); chaos = evidence("chaos.json"); ver = evidence("verify-report.json")
amd = evidence("amd64-verify-report.json")

# ---- flush sensitivity rows, read from the model output
sens_block = cost.split("Sensitivity:")[1].split("Buffer alternative")[0]
sens = re.findall(r"^\s+([\d.]+)s\s+([\d.,]+) M\s+\$([\d,]+)\s+~([\d.]+) s", sens_block, re.M)
assert len(sens) == 6
sens_rows = "\n".join(f'<tr><th scope="row">{f} s</th><td class="num">{p} M</td><td class="num">${u}</td><td class="num">about {l} s</td></tr>' for f, p, u, l in sens)

# ---- comparison table (module 1 shows the same figures)
def rng(a, b):
    return f"${a:,.0f}" if abs(a - b) < 1 else f"${a:,.0f} to ${b:,.0f}"
cmp_rows = []
for r in cmp_["cases"]:
    name = r["case"].strip()
    sv = "" if name.startswith("A.") else rng(r["saving_vs_current_low"], r["saving_vs_current_high"])
    cmp_rows.append(f'<tr><td>{esc(name)}</td><td class="num">{rng(r["infra_low"], r["infra_high"])}</td><td class="num">{rng(r["total_low"], r["total_high"])}</td><td class="num">{sv}</td></tr>')
cmp_table = ('<table class="data"><thead><tr><th scope="col">Case (monthly, USD)</th><th scope="col" class="num">Infrastructure</th><th scope="col" class="num">Total with assumed engineer time</th>'
             '<th scope="col" class="num">Saving against current</th></tr></thead><tbody>\n' + "\n".join(cmp_rows) + '\n</tbody></table>')

LIMITS = [
    ("No run on real AWS S3", "Everything was proved against the local object store RustFS. The behaviour of the Buffer, the retry window and the conditional writes on AWS S3 has not been observed.", "Run the stack against an S3 bucket with the IAM permissions listed below, and repeat the verification and failure tests. This needs AWS access and an explicit decision to spend."),
    ("No load test at production volume", "The reference workload is about 1,100 events a second plus 7,600 metric samples a second. The tests used a small request mix on one machine, so throughput, queue depth and ClickHouse sizing at that volume are judgements.", "A sustained load test at that rate, measuring latency, queue depth and ClickHouse CPU and memory."),
    ("No run on real x86 hardware", f"The amd64 images ran only under emulation on an Apple Silicon host, where verification passed {amd['passed']} of {len(amd['checks'])}. They were built before the checkout flow was added, so that run does not cover it. They have not run on real x86 hardware. Source: <code>evidence/amd64-verify-report.json</code>.", "Run the verification on a real x86 host."),
    ("Collector Go modules not audited", "The collector's Go dependencies have had no supply-chain audit.", "Audit the modules, then pin and record the result."),
    ("Duplicates on the first hop are not removed", "The idempotency token covers the path from the Buffer to ClickHouse. A duplicate sent twice from the application to the collector enters as two records and is stored twice.", "Treat counts as at-least-once for that hop, or deduplicate on a stable event id at query time."),
    ("A collector crash loses its in-memory batches", "The collector acknowledges the application before data is durable in some stages. What it holds when it crashes is lost.", "A design that makes data durable before it is acknowledged, followed by a crash test under load. The failure tests today restart the collector only while idle."),
    ("A value ClickHouse rejects blocks a signal", "A valid OTLP value that ClickHouse rejects, such as a timestamp past the year 2262, is retried forever and blocks that signal's queue.", "A rule that sets aside a chunk after repeated rejection, with a test that sends such a value."),
    ("Alerts miss late rows", "HyperDX evaluates an alert once a minute at second :00 for the minute just ended, with no allowance for ingest delay. Rows that arrive after :00 are never counted.", "An alert window that lags behind the ingest delay, or a check that compares against a delayed window."),
    ("HyperDX external API rate limit", "The API accepts 100 requests per window. The verifier backs off on a 429 response.", "Confirm the limit against the number of dashboards and alerts you will manage as code."),
    ("HyperDX multi-team, RBAC and SSO unverified", "These features were not exercised.", "Test them against your actual identity and team requirements."),
    ("An object-store outage longer than the retry window loses data", "The exporter retries a failed Buffer write for a bounded time, 5 minutes by default. If the object store is down for longer, the batch is dropped. Outages of 8 s and 30 s were tested and lost nothing.", "An outage test longer than the retry window, and a decision on whether to buffer to disk in the collector."),
    ("Buffer producer stall beyond 30 s is untested", "The Buffer producer has an internal manifest-retry budget. If it were exhausted, a signal could stall until the collector restarts. This was not reproduced at 30 s and not tested beyond.", "An outage test longer than 30 s, watching the <code>buffer.producer.halted</code> gauge."),
    ("One ingestor replica per signal", "Running several consumers on one manifest has not been confirmed, so the mapping says one replica per signal.", "A multi-consumer test on one manifest."),
    ("ClickHouse Cloud rates unconfirmed and untested", "The Cloud prices in the model came from a page summary and are marked unverified, and no run used ClickHouse Cloud.", "Confirm prices with the provider and run the platform against a Cloud service."),
    ("Environment tiers not built", "The tier design below is a proposal.", "Build it for one non-production tier and measure the operating cost."),
]
lim_rows = "\n".join(f'<tr><th scope="row">{a}</th><td>{b}</td><td>{c}</td></tr>' for a, b, c in LIMITS)

body = f'''  <section aria-labelledby="limits">
    <h2 id="limits">What has not been verified</h2>
    <p>The local stack in <code>stack/</code> is the learning and trial environment. It passed {ver["passed"]} of {len(ver["checks"])} verification checks, {len(chaos)} failure scenarios and {alerts["passed"]} alert checks on one machine, and the list below is what those tests do not cover. Each limit is stated plainly with what would close it.</p>
    <table class="data">
      <thead><tr><th scope="col">Limit</th><th scope="col">What is true</th><th scope="col">What would close it</th></tr></thead>
      <tbody>
{lim_rows}
      </tbody>
    </table>
    <aside class="caveat"><p><strong>Read the cost figures with these limits.</strong> The cost model is built from assumptions and AWS list prices. Nothing in it was measured on AWS, and no production volume was tested.</p></aside>
  </section>

  <section aria-labelledby="tiers">
    <h2 id="tiers">Environment tiers: a proposal</h2>
    <aside class="caveat"><p><strong>Proposal, not built.</strong> Nothing in this section has been implemented or tested. The stack today has one tier, and the test application sets <code>deployment.environment.name=local</code>.</p></aside>
    <p>The question is whether production and non-production telemetry should share one ClickHouse and one Buffer or be stored apart. The proposal is a middle path: one interface for people to query, separate processing and storage per tier, and the environment still carried as an attribute on every record.</p>
    <ul>
      <li><strong>Prod tier.</strong> Its own collectors, Buffer bucket, ingestors and ClickHouse, sized and retained for production needs.</li>
      <li><strong>Non-prod tier.</strong> The same components, smaller, with shorter retention and looser access.</li>
      <li><strong>One interface.</strong> HyperDX, or a query layer in front of both ClickHouse services, so a user searches one place and chooses an environment.</li>
      <li><strong>Environment attribute.</strong> Records keep <code>deployment.environment.name</code> even inside a tier, so filtering by environment works in either design.</li>
    </ul>
    <table class="data">
      <thead><tr><th scope="col">Concern</th><th scope="col">Stored together</th><th scope="col">Stored separately by tier</th></tr></thead>
      <tbody>
        <tr><th scope="row">Isolation</th><td>A noisy non-prod load or a heavy query shares capacity with production.</td><td>Non-prod load cannot slow production ingestion or queries.</td></tr>
        <tr><th scope="row">Failure blast radius</th><td>One ClickHouse or queue problem affects every environment.</td><td>A problem stays inside one tier.</td></tr>
        <tr><th scope="row">Retention and access</th><td>One policy per table, so environment-specific rules rely on filters.</td><td>Each tier has its own retention and its own read-only users.</td></tr>
        <tr><th scope="row">Cost and operations</th><td>One set of components to run and pay for.</td><td>Fixed parts such as compute, coordination and collectors are duplicated, and there are two systems to operate.</td></tr>
        <tr><th scope="row">Cross-environment view</th><td>One query covers everything.</td><td>Needs the shared interface, and a query across tiers means two queries.</td></tr>
      </tbody>
    </table>
  </section>

  <section aria-labelledby="aws">
    <h2 id="aws">Mapping the local stack to AWS</h2>
    <p>The mapping below is from <code>stack/README.md</code>. It has been written down and has not been executed.</p>
    <table class="data">
      <thead><tr><th scope="col">Local</th><th scope="col">AWS</th><th scope="col">What changes</th></tr></thead>
      <tbody>
        <tr><td>RustFS</td><td>S3 bucket</td><td>Unset <code>S3_ENDPOINT</code> or <code>AWS_ENDPOINT</code>, <code>AWS_ALLOW_HTTP</code> and <code>AWS_VIRTUAL_HOSTED_STYLE_REQUEST</code>. Credentials come from the task or pod IAM role, which needs <code>s3:GetObject</code>, <code>s3:PutObject</code>, <code>s3:DeleteObject</code> (the Buffer removes consumed objects) and <code>s3:ListBucket</code> on the bucket.</td></tr>
        <tr><td>Collector container</td><td>ECS or EKS deployment, stateless, scaled horizontally</td><td><code>BUFFER_BUCKET</code> and <code>AWS_REGION</code>, with the same config file.</td></tr>
        <tr><td><code>ingestor-*</code></td><td>ECS or EKS, one deployment per signal</td><td><code>INGESTOR__CLICKHOUSE__ENDPOINT</code>, <code>__USER</code>, <code>__PASSWORD</code> and <code>INGESTOR__BUFFER__OBJECT_STORE__BUCKET</code>. One replica per signal.</td></tr>
        <tr><td>ClickHouse container</td><td>ClickHouse Cloud, or EC2 or EKS</td><td>Apply <code>clickhouse/*.sql</code>, using <code>Replicated*</code> engines on a cluster, and create <code>hyperdx_ro</code>.</td></tr>
        <tr><td>HyperDX and MongoDB</td><td>ECS or EKS plus a MongoDB</td><td><code>MONGO_URI</code> and <code>FRONTEND_URL</code>, and run <code>provision.py</code> with the <code>HDX_*</code> and <code>CH_*</code> variables.</td></tr>
        <tr><td>Development credentials</td><td>A secrets service such as Secrets Manager or SSM</td><td>No development credential is meant to be used outside the local stack.</td></tr>
      </tbody>
    </table>
    <p>Part of the verification is reusable on AWS, since <code>verify.py</code> reads the ClickHouse and HyperDX addresses from variables. The load generator and <code>chaos.py</code> are tied to the local Compose project and would need adapting.</p>
  </section>

  <section aria-labelledby="levers">
    <h2 id="levers">Cost levers</h2>
    <p>Module 1 explains why search scanning dominates the current bill, and this is the same comparison in compact form. Infrastructure uses AWS on-demand prices in the Sydney region, and the people-cost column is an assumption.</p>
    <div class="evid">
      {cmp_table}
      <p class="src">Source: <code>evidence/comparison.json</code>, from <code>cost/compare.py</code>, captured {man["comparison.json"]["captured_at"]}. See <a href="01-problem-and-outcome.html">module 1</a> for the assumptions behind each case.</p>
    </div>
    {bill_history()}
    <aside class="caveat"><p><strong>Sensitive figures.</strong> These amounts come from one organisation's bill. Replace them with your own, or remove this table, before the course is shared outside that organisation.</p></aside>
    <h3>Flush interval</h3>
    <p>The collector's flush interval sets how many objects it writes to S3. Every flush costs PUT requests, and a short interval at a high event rate becomes the largest S3 cost. The model computes it as follows for the base scenario:</p>
<pre><code>PUTs per month = max(2,592,000 / flush_s,
                     buffer bytes per collector per signal / flush_size_bytes)
                 x collectors x signals x 1.25
GETs per month = objects written x 1 + manifest polls (2,592,000 / poll_s x signals)</code></pre>
    <p>The constants are the model's. There are 3 collectors and 2 signals (logs and metrics, since traces had no volume figure), <code>flush_size_bytes</code> is 4 MiB, the factor 1.25 is one data object plus one manifest write per four batches, and the ingestors poll every 1 second. Buffer volume assumes 650 bytes per log and 80 bytes per metric sample in OTLP, compressed 5 times for logs and 4 times for metrics. The prices are $0.0055 per 1,000 PUTs and $0.0044 per 10,000 GETs in Sydney. All of the sizing constants are assumptions, and nothing was measured on AWS.</p>
    <div class="sim" id="fl-panel">
      <div class="controls">
        <label class="ctl">Collector flush interval
          <input type="range" id="fl-slider" min="0" max="9" step="1" value="5"></label>
        <span class="ctl">Selected <output id="fl-val" for="fl-slider">5 s</output></span>
      </div>
      <p class="out" id="fl-out" aria-live="polite">At a 5 s flush the model gives about 3.9 million PUTs and about $25 of S3 requests a month. A 0.1 s flush gives about 194 million PUTs and about $1,140.</p>
      <table class="data">
        <thead><tr><th scope="col">Flush interval</th><th scope="col" class="num">PUTs a month</th><th scope="col" class="num">S3 requests a month</th><th scope="col" class="num">Latency added by the flush</th></tr></thead>
        <tbody id="fl-tbody">
{sens_rows}
        </tbody>
      </table>
      <p class="tag">Without JavaScript the table shows the six rows printed by the model. With JavaScript it is recomputed with the same formula and adds the intervals 0.25, 2, 60 and 120 s.</p>
    </div>
    <p>The local stack flushes every 0.1 s, and measured latency from event to queryable is a median of about 0.6 s for spans, with a 99th percentile of about 4.5 s. A longer flush saves S3 requests and adds roughly its own length to the delay. The 120 s row in the slider shows the other limit: with these assumed volumes, beyond roughly 80 to 90 s the size-based term in the formula is larger than the time-based one, and the request count stops falling with the interval.</p>
    <h3>Scan guard-rails</h3>
    <p>Search is the other big lever: a per-scan price turns every broad query into a cost, and self-hosted ClickHouse turns it into load. The general idea is to put limits on what one query or one user can read: a cap on rows or bytes read per query, a cap on query time and time range, and a per-role quota over a period, with a separate role for automated reports. ClickHouse has settings and quotas for these. They were not configured or tested in this stack, so treat this as a design idea and not as a result.</p>
  </section>'''

q = quiz([
    {"q": "The model's base scenario flushes every 5 seconds. A team changes the flush to 0.5 seconds to cut latency. What does the model predict for PUT requests, and what is the trade-off?",
     "options": [
        ("About ten times as many PUTs, and roughly a tenth of the added delay.", "The time-driven term scales with the inverse of the interval, so the PUT count and the S3 request cost rise by a similar factor while the delay shrinks."),
        ("About the same number of PUTs, because object count depends on data volume.", "At these rates the flush timer, not the data volume, decides when an object is written. Revisit the formula's first term."),
        ("Fewer PUTs, because smaller batches are cheaper to write and need fewer requests.", "Each flush is a request regardless of size, so more flushes mean more requests. Revisit what a PUT is billed on."),
     ], "answer": 0},
    {"q": "Which statement about this platform is supported by the evidence?",
     "options": [
        ("It keeps all data across a collector crash during load, because the Buffer on S3 is durable and replicated.", "The Buffer is durable only after the write succeeds, and batches in the collector's memory are lost. Revisit the collector limit in the table."),
        ("It was load-tested at production volume and needs only a configuration change for AWS.", "No test ran at production volume, and the AWS mapping is documented and not executed. Revisit the limits table."),
        ("It has lost no data in the tested failure scenarios on one machine, and has not run on real AWS S3.", "Both halves are stated limits of the evidence: the scenarios were tested locally, and the AWS run has not happened."),
     ], "answer": 2},
    {"q": "A team wants production and non-production telemetry in one place for developers, but non-production load tests keep slowing production queries. Which design does the proposal suggest?",
     "options": [
        ("Keep one ClickHouse and filter by environment in every query, so the load is shared fairly between tiers.", "A filter limits what a query returns and does not give it its own capacity, so the contention remains. Revisit the isolation row of the trade-offs table."),
        ("Separate storage and processing per tier behind one shared interface, keeping environment as an attribute.", "Separate capacity removes the interference, the shared interface keeps a single place to search, and the attribute keeps filtering possible."),
        ("Move non-production data to the same tier but with a shorter TTL, so that it uses less of the shared space.", "Retention changes how long data stays and not which capacity serves queries. Revisit what separate storage per tier provides."),
     ], "answer": 1},
], gate="All correct. You have finished the Running it track. Return to the course home to choose another track.")

prev, nxt = neighbours("m11")
write("11-limits-tiers-and-aws.html", page(
    "11-limits-tiers-and-aws.html", "m11", "Limits, tiers and the road to AWS", "Running it", "5 of 5",
    "What has and has not been verified, how environments could be stored together or separately, and how the local stack maps to AWS.",
    ["list what has and has not been verified,",
     "weigh storing environments together against storing them separately, and",
     "map the local stack to AWS and see which settings drive the cost."],
    body, q, scripts=["sim-flush.js"], mermaid=False, prev=prev, next=nxt))
