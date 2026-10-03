#!/usr/bin/env python3
import re
from lib import page, quiz, write, neighbours, evidence, esc

ver = evidence("verify-report.json"); selft = evidence("selftest.json"); chaos = evidence("chaos.json")
gt = evidence("ground-truth.json"); man = evidence("manifest.json")
replays = evidence("chaos-ingestor-crash.txt")
attempts = re.findall(r"dropped as duplicates by ClickHouse during this scenario: (\d+)", replays)

# ---- ground truth table
pe = gt["per_endpoint"]
ORDER = ["ok", "slow", "error", "exception", "chain", "chain-fail", "checkout"]
rows = []
for k in ORDER:
    e = pe[k]
    fail = e["count"] if e["expected_status"] >= 500 else 0
    rows.append(f'<tr><td><code>/{k}</code></td><td class="num">{e["count"]}</td><td class="num">{e["expected_status"]}</td>'
                f'<td class="num">{e["count"] * e["expected_spans_each"]}</td><td class="num">{e["count"] * e["expected_logs_each"]}</td><td class="num">{fail}</td></tr>')
t = gt["totals"]
assert sum(pe[k]["count"] * pe[k]["expected_spans_each"] for k in pe) == t["expected_spans"]
assert sum(pe[k]["count"] * pe[k]["expected_logs_each"] for k in pe) == t["expected_logs"]
rows.append(f'<tr><th scope="row">Total</th><td class="num">{t["requests"]}</td><td></td><td class="num">{t["expected_spans"]}</td><td class="num">{t["expected_logs"]}</td><td class="num">{t["expected_http_5xx"]}</td></tr>')
gt_table = ('<table class="data"><thead><tr><th scope="col">Endpoint</th><th scope="col" class="num">Requests</th><th scope="col" class="num">Status</th>'
            '<th scope="col" class="num">Expected spans</th><th scope="col" class="num">Expected logs</th><th scope="col" class="num">Expected 5xx</th></tr></thead><tbody>\n'
            + "\n".join(rows) + '\n</tbody></table>')

# ---- check families
FAM = {"G": "Ground truth", "A": "Pipeline", "B": "Tracing", "D": "HyperDX", "E": "Latency"}
by = {k: [] for k in FAM}
for c in ver["checks"]:
    by[c["id"][0]].append(c)
assert sum(len(v) for v in by.values()) == len(ver["checks"])
EX = {"G": ["G1"], "A": ["A2", "A4t"], "B": ["B5", "B8"], "D": ["D13b", "D14a"], "E": []}
fam_rows = []
for k, name in FAM.items():
    exs = [c for c in by[k] if c["id"] in EX[k]]
    if k == "E":
        ex_html = "<code>E16</code> End-to-end latency for every row: 99th percentile within 15 s."
    else:
        ex_html = "<br>".join(f'<code>{c["id"]}</code> {esc(c["desc"])}' for c in exs)
    fam_rows.append(f'<tr><td><code>{k}</code> {name}</td><td class="num">{len(by[k])}</td><td>{ex_html}</td></tr>')
fam_table = ('<table class="data"><thead><tr><th scope="col">Family</th><th scope="col" class="num">Checks</th><th scope="col">Example</th></tr></thead><tbody>\n'
             + "\n".join(fam_rows) + '\n</tbody></table>')

# ---- self test
st_rows = "\n".join(f'<tr class="{"is-ok" if m["detected"] else "is-bad"}"><td>{esc(m["mutation"])}</td><td>{", ".join(m["must_fail"])}</td><td>{", ".join(m["failed"])}</td></tr>' for m in selft["mutations"])
LABEL = {"chaos-ingestor-crash": "All ingestors killed (SIGKILL) during load", "chaos-ingestor-restart": "All ingestors restarted during load",
         "chaos-clickhouse-outage": "ClickHouse stopped for 20 s during load", "chaos-s3-blip": "Object store stopped for 8 s during load",
         "chaos-s3-outage-long": "Object store stopped for 30 s during load", "chaos-bad-payload": "Garbage entries written into all three queues during load",
         "chaos-collector-restart-idle": "Collector restarted while idle, then a full verification"}
ch_rows = "\n".join(f'<tr class="{"is-ok" if v["failed"] == 0 else "is-bad"}"><td>{LABEL[k]}</td><td class="num">{v["passed"]} of {v["checks"]}</td></tr>' for k, v in chaos.items())

svg = '''<svg viewBox="0 0 800 170" role="img" aria-labelledby="flow-t flow-d">
<title id="flow-t">Data-flow explorer pipeline</title>
<desc id="flow-d">Applications send rows to the collector, which writes them to the Buffer on S3. The ingestor reads the Buffer and inserts into ClickHouse. The numbers under each stage update as you step time.</desc>
<defs><marker id="flow-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path class="d-arrowhead" d="M0 0 L10 5 L0 10 z"/></marker></defs>
<path class="d-edge d-edge--live" id="flow-edge-live" d="M135 55 L168 55" marker-end="url(#flow-arr)"/>
<path class="d-edge" d="M295 55 L328 55" marker-end="url(#flow-arr)"/>
<path class="d-edge" d="M455 55 L488 55" marker-end="url(#flow-arr)"/>
<path class="d-edge" d="M615 55 L648 55" marker-end="url(#flow-arr)"/>
''' + "\n".join(
    f'<rect class="d-node" id="{rid}" x="{x}" y="20" width="125" height="70" rx="4"/>'
    f'<text class="d-text" x="{x + 62}" y="42">{name}</text><text class="d-label d-text" id="{tid}" x="{x + 62}" y="68">0</text>'
    f'<text class="d-tick" x="{x + 62}" y="112" text-anchor="middle">{cap}</text>'
    for rid, tid, x, name, cap in [
        ("flow-apps", "flow-t-apps", 10, "Apps", "rows produced"),
        ("flow-collector", "flow-t-collector", 170, "Collector", "rows in memory"),
        ("flow-buffer", "flow-t-buffer", 330, "Buffer (S3)", "rows queued now"),
        ("flow-ingestor", "flow-t-ingestor", 490, "Ingestor", "state"),
        ("flow-clickhouse", "flow-t-clickhouse", 650, "ClickHouse", "rows stored")]) + '\n<text class="d-tick" id="flow-t-written" x="392" y="136" text-anchor="middle">0 written in total</text>\n</svg>'

body = f'''  <section aria-labelledby="gt">
    <h2 id="gt">Ground truth</h2>
    <p>A test of a telemetry pipeline needs to know what should have arrived, and it cannot learn that from the pipeline. The load generator sends a fixed request mix to the test application and records, for each endpoint, how many requests it sent, the status it expects, and the exact number of spans and log lines each request must produce. This record is the <dfn>ground truth</dfn>, and the checks compare ClickHouse and HyperDX with it.</p>
    <div class="evid">
      {gt_table}
      <p class="src">Source: the load generator's contract for run <code>{gt["run_id"]}</code>, from <code>evidence/ground-truth.json</code>. Expected availability for this run is {t["expected_availability"]:.1%}: {t["requests"] - t["expected_http_5xx"]} of {t["requests"]} server spans are not 5xx.</p>
    </div>
    <p>The numbers are exact, so a single lost span or an extra log line fails a check. They are known without reading telemetry: <code>/chain</code> produces 4 spans and 2 logs by construction, and the generator counts how many times it called it. If the checks derived expectations from ClickHouse, a pipeline that dropped 5% of the data would still agree with itself.</p>
  </section>

  <section aria-labelledby="families">
    <h2 id="families">The check families</h2>
    <p>The verifier, <code>stack/verify/verify.py</code>, runs {len(ver["checks"])} checks in five families. Each family asks a different question about the same run.</p>
    <div class="evid">
      {fam_table}
      <p class="src">Source: run <code>{ver["run_id"]}</code> in <code>evidence/verify-report.json</code>, captured {man["verify-report.json"]["captured_at"]}. All {ver["passed"]} checks passed.</p>
    </div>
    <p>The pipeline checks compare counts per service, severity, span kind and route with the ground truth. The duplicate checks require the raw row count, the <code>FINAL</code> row count and the number of unique provenance keys to be equal, which is how module 7's deduplication is tested. The HyperDX checks ask HyperDX's own API, so they test what a user would see and not only what is stored.</p>
  </section>

  <section aria-labelledby="negative">
    <h2 id="negative">Testing the tests</h2>
    <p>A check that has only ever passed has not been shown to detect anything. The self-test, <code>stack/verify/selftest.py</code>, runs a clean verification, then deliberately damages the stored data and requires the right checks to fail. All {len(selft["mutations"])} corruptions were detected.</p>
    <div class="evid">
      <table class="data"><thead><tr><th scope="col">Corruption</th><th scope="col">Must fail</th><th scope="col">Failed</th></tr></thead><tbody>
{st_rows}
      </tbody></table>
      <p class="src">Source: <code>evidence/selftest.json</code>, captured {man["selftest.json"]["captured_at"]}. Every check in the "Must fail" column also appears in "Failed", and other checks failed too.</p>
    </div>
    <p>Negative testing found three defects in the verifier itself, each a way for the suite to report success when it should not have. They are lessons for any test suite:</p>
    <ul>
      <li><strong>An early exit hid other checks.</strong> The run stopped at the first failure, so later checks never ran and a broken run looked narrower than it was. Run every check and report all results.</li>
      <li><strong>A crash was masked by a stale report.</strong> When the verifier crashed, an older report was still on disk and was read as the result. A run must clear or ignore any output it did not produce.</li>
      <li><strong>Reusing a run id silently tripled every count.</strong> Data from earlier runs with the same id was counted again. A test must refuse to start when its precondition is unsafe, and module 10 comes back to this.</li>
    </ul>
  </section>

  <section aria-labelledby="chaos">
    <h2 id="chaos">Failure injection</h2>
    <p>The failure tests in <code>stack/verify/chaos.py</code> send load, break one component part-way through, wait for the system to settle, and run the same count and duplicate checks. A failure that loses or repeats data fails them.</p>
    <div class="evid">
      <table class="data"><thead><tr><th scope="col">Failure injected</th><th scope="col" class="num">Checks passed</th></tr></thead><tbody>
{ch_rows}
      </tbody></table>
      <p class="src">Source: <code>evidence/chaos.json</code>, captured {man["chaos.json"]["captured_at"]}.</p>
    </div>
    <aside class="note"><p><strong>Replays depend on timing.</strong> Killing an ingestor only causes a replay if the kill lands between an insert and its acknowledgement. In the three attempts captured for this course ClickHouse dropped {", ".join(attempts)} replayed inserts (the count varies from run to run). Every attempt ended with exact counts, so the evidence is that nothing was lost or duplicated, and a single run may see no replay at all.</p></aside>
  </section>

  <section aria-labelledby="explorer">
    <h2 id="explorer">Data-flow explorer</h2>
    <p>The explorer is a model of the pipeline, with one step representing one second and the applications producing {10} rows a second. In a healthy run the ingestor empties the queue within the same second, so the queue reads 0 now while the running total under Buffer shows how many rows have been written to it. Stop the ingestor, step time, and watch the queue build up; stop another stage and watch where the rows wait. The behaviour follows the documented design and the failure tests above. The rates are illustrative, and the model does not call any real service.</p>
    <div class="sim" id="flow-panel">
      <figure>
        {svg}
        <figcaption>Rows flow left to right. A stopped stage is drawn with a warning fill and the toggle buttons carry the same state in text.</figcaption>
      </figure>
      <p class="controls-row">
        <button type="button" class="btn" id="flow-b-s3" aria-pressed="false">Stop S3</button>
        <button type="button" class="btn" id="flow-b-ingestor" aria-pressed="false">Stop ingestor</button>
        <button type="button" class="btn" id="flow-b-clickhouse" aria-pressed="false">Stop ClickHouse</button>
        <button type="button" class="btn" id="flow-b-traffic" aria-pressed="false">Stop traffic</button>
      </p>
      <p class="controls-row">
        <button type="button" class="btn" id="flow-b-kill">Kill ingestor mid-commit</button>
        <button type="button" class="btn" id="flow-b-crash">Crash collector</button>
        <button type="button" class="btn" id="flow-b-step1">Step 1 s</button>
        <button type="button" class="btn" id="flow-b-step10">Step 10 s</button>
        <button type="button" class="btn" id="flow-b-step60">Step 60 s</button>
        <button type="button" class="btn" id="flow-b-reset">Reset</button>
      </p>
      <p class="out" id="flow-summary" aria-live="polite">Time 0 s. Press a step button to start.</p>
      <p class="readout" id="flow-notes" aria-live="polite"></p>
      <table class="steps"><thead><tr><th scope="col">Time (s)</th><th scope="col">Action</th><th scope="col">In collector memory</th><th scope="col">Queue depth</th><th scope="col">In ClickHouse</th><th scope="col">Dropped as duplicates</th><th scope="col">Lost</th></tr></thead><tbody id="flow-history"></tbody></table>
    </div>
    <table class="data">
      <thead><tr><th scope="col">Fault</th><th scope="col">What happens to the data</th><th scope="col">Tested?</th></tr></thead>
      <tbody>
        <tr><td>Object store down for less than the retry window</td><td>The collector holds the batches and retries the write, then delivers them. Nothing was lost in the tested outages of 8 s and 30 s. Outages between 30 s and 5 minutes are untested (see module 11).</td><td>Yes, for 8 s and 30 s under load.</td></tr>
        <tr><td>Object store down for more than 5 minutes</td><td>The retry limit is reached and the batch is dropped (a modelled case).</td><td>No. The default retry window is documented, and the model extrapolates from it.</td></tr>
        <tr><td>Ingestor stopped</td><td>Rows wait in the Buffer and are inserted after the restart. Delay, no loss.</td><td>Yes, kill and restart under load.</td></tr>
        <tr><td>ClickHouse down</td><td>The ingestors retry and the queue grows. Delay, no loss.</td><td>Yes, 20 s under load.</td></tr>
        <tr><td>Ingestor killed between insert and acknowledge</td><td>The range is read again after the restart and ClickHouse drops the repeat by token.</td><td>Yes, but a replay happens only when the timing lines up.</td></tr>
        <tr><td>Collector crash</td><td>The batch in its memory is lost. This is a known loss case and a stated limit.</td><td>Not under load: the collector is restarted only while idle.</td></tr>
      </tbody>
    </table>
    <aside class="caveat"><p><strong>The collector crash is a real loss.</strong> The collector acknowledges the application before a batch reaches the Buffer, so what it holds in memory when it dies is gone. Module 11 lists this with the other open limits. Without JavaScript, the table above describes each fault.</p></aside>
  </section>'''

q = quiz([
    {"q": "A verifier derived its expected span counts by counting spans in ClickHouse, then compared ClickHouse with that count. The pipeline silently dropped 5% of the spans. What would the check report?",
     "options": [
        ("A failure, because the dropped spans make the two counts differ by about 5%.", "Both sides of the comparison come from the same stored data, so the loss is on both sides. Revisit where the ground truth comes from."),
        ("A pass, because the expectation was taken from the data it was checking.", "An expectation that is read back from the system cannot disagree with it, and the loss stays invisible."),
        ("A failure, but only in the duplicate checks, which compare raw rows with unique keys.", "Duplicate checks compare raw rows with unique keys and say nothing about missing rows. Revisit what each check family compares."),
     ], "answer": 1},
    {"q": "ClickHouse is stopped for 20 seconds while load continues, and then started again. What do you observe?",
     "options": [
        ("The queue depth rises during the outage and then falls, and the final counts equal what was sent.", "The Buffer absorbs the backlog and the ingestors insert it after the restart, which is what the exact counts show."),
        ("The ingestors drop the rows they cannot insert, so the final counts are short by the outage's share.", "The ingestor acknowledges a range only after ClickHouse accepts it, so a failed insert leaves the range in the queue. Revisit where the data waits."),
        ("The collector stops accepting data because ClickHouse is unreachable and the applications start failing.", "The collector writes to the Buffer and is not connected to ClickHouse. Revisit the data path."),
     ], "answer": 0},
    {"q": "Why does the failure test restart the collector only while it is idle, and not under load?",
     "options": [
        ("A collector restart under load corrupts the manifest, so the queue cannot be read afterwards by an ingestor.", "The manifest is updated with compare-and-set and does not depend on the collector staying up. Revisit the module 7 sequence."),
        ("The collector cannot restart while it is receiving data, because the receiver port stays locked.", "Restarting is possible at any time, and the issue is what happens to data in flight. Revisit the loss case in the explorer."),
        ("Under load a crash would lose batches held in memory, so exact counts would fail for another reason.", "Batches in memory are not yet durable, and the exact-count checks would then fail for a known limit."),
     ], "answer": 2},
], gate="All correct. Continue to module 10, what broke and what we learned.")

prev, nxt = neighbours("m09")
write("09-how-we-know-it-works.html", page(
    "09-how-we-know-it-works.html", "m09", "How we know it works", "Running it", "3 of 5",
    "How a test with known expected results proves the pipeline, how the tests were tested, and what each injected failure does to the data path.",
    ["explain how ground truth makes a test meaningful,",
     "explain why the test suite itself is tested, and",
     "predict what each failure does to the data path."],
    body, q, scripts=["sim-dataflow.js"], mermaid=False, prev=prev, next=nxt))
