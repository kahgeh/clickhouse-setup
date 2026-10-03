#!/usr/bin/env python3
import ast, json, re
from lib import page, quiz, write, neighbours, evidence, esc

ver = evidence("verify-report.json"); selft = evidence("selftest.json"); chaos = evidence("chaos.json")
alerts = evidence("alerts-report.json"); man = evidence("manifest.json")
gt = evidence("ground-truth.json")

FAMILY = {"G": "G", "R": "A", "E": "E", "A": "A", "B": "B", "D": "D"}
def family(cid):
    return cid[0] if cid[0] in "ABDEG" else "A"
NAMES = {"A": "Pipeline", "B": "Tracing", "D": "HyperDX", "E": "Latency", "G": "Ground truth"}

# --- latency from the E16 description ("measured {...}")
e16 = next(c for c in ver["checks"] if c["id"] == "E16")
figs = ast.literal_eval(re.search(r"measured (\{.*\})", e16["desc"]).group(1))

kpis = [
    (f'{ver["passed"]} of {len(ver["checks"])}', "verification checks pass"),
    (f'{sum(1 for m in selft["mutations"] if m["detected"])} of {len(selft["mutations"])}', "deliberate corruptions detected"),
    (f'{sum(1 for v in chaos.values() if v["failed"] == 0)} of {len(chaos)}', "failure scenarios pass"),
    (f'{alerts["passed"]} of {len(alerts["checks"])}', "alert checks pass"),
    (f'{figs["spans"]["p99_s"]:.1f} s', "99th percentile span latency, event to queryable"),
]
kpi_html = '<div class="kpi-row">' + "".join(f'<div class="kpi"><span class="big">{a}</span><span class="what">{b}</span></div>' for a, b in kpis) + '</div>'

rows = []
for c in ver["checks"]:
    f = family(c["id"])
    desc = c["desc"]
    if c["id"] == "E16":
        desc = "End-to-end latency, every row: 99th percentile within 15 s (measured values are in the table below)"
    rows.append(f'<tr class="{"is-ok" if c["ok"] else "is-bad"}" data-family="{f}"><td><code>{c["id"]}</code></td><td>{esc(desc)}</td><td><span class="pill {"pill--ok" if c["ok"] else "pill--bad"}">{"pass" if c["ok"] else "fail"}</span></td></tr>')
filters = '<p class="controls-row">' + " ".join(f'<button type="button" class="btn btn--tab" data-filter="{k}" aria-pressed="false">{v}</button>' for k, v in [("all", "All")] + [(k, NAMES[k]) for k in "GABDE"]) + '</p>'
verify_table = (f'{filters}\n<p class="tag" id="verify-count" aria-live="polite"></p>\n<table class="data" id="verify-table">\n<thead><tr><th scope="col">Check</th><th scope="col">What it asserts</th><th scope="col">Result</th></tr></thead>\n<tbody>\n'
                + "\n".join(rows) + '\n</tbody>\n</table>')

selftest_rows = "\n".join(
    f'<tr class="{"is-ok" if m["detected"] else "is-bad"}"><td>{esc(m["mutation"])}</td><td>{", ".join(m["must_fail"])}</td><td>{", ".join(m["failed"])}</td></tr>' for m in selft["mutations"])

LABEL = {"chaos-ingestor-crash": "All ingestors killed (SIGKILL) during load", "chaos-ingestor-restart": "All ingestors restarted during load",
         "chaos-clickhouse-outage": "ClickHouse stopped for 20 s during load", "chaos-s3-blip": "Object store stopped for 8 s during load",
         "chaos-s3-outage-long": "Object store stopped for 30 s during load", "chaos-bad-payload": "Garbage entries written into all three queues during load",
         "chaos-collector-restart-idle": "Collector restarted while idle, then a full verification"}
chaos_rows = "\n".join(f'<tr class="{"is-ok" if v["failed"] == 0 else "is-bad"}"><td>{LABEL.get(k, k)}</td><td class="num">{v["passed"]} of {v["checks"]}</td></tr>' for k, v in chaos.items())

def ms(m):
    return f'<tr><td>{m}</td><td class="num">{figs[m]["rows"]}</td><td class="num">{figs[m]["p50_s"]:.2f} s</td><td class="num">{figs[m]["p99_s"]:.2f} s</td><td class="num">{figs[m]["max_s"]:.2f} s</td></tr>'
lat_table = ('<table class="data"><thead><tr><th scope="col">Signal</th><th scope="col" class="num">Rows</th><th scope="col" class="num">Median</th>'
             '<th scope="col" class="num">99th percentile</th><th scope="col" class="num">Slowest</th></tr></thead><tbody>' + ms("spans") + ms("logs") + '</tbody></table>')

dash = man["screens/dash-availability.png"]; alertshot = man["screens/alerts.png"]
replays = evidence("chaos-ingestor-crash.txt")
attempts = re.findall(r"dropped as duplicates by ClickHouse during this scenario: (\d+)", replays)
body = f'''  <section aria-labelledby="headline">
    <h2 id="headline">The headline results</h2>
    <p>Every result below was produced by scripts in the repository, against the platform running on one laptop, and each number on this page is read from a file captured from those runs. The load generator sends a known mix of requests and writes down what it sent. The checks compare what arrived with what was sent, and never with the telemetry being checked.</p>
    {kpi_html}
    <p>The run behind these numbers sent {gt["totals"]["requests"]} requests and expected {gt["totals"]["expected_spans"]} spans, {gt["totals"]["expected_logs"]} log lines, and {gt["totals"]["expected_http_5xx"]} failures, which makes the expected availability {gt["totals"]["expected_availability"]:.1%}.</p>
  </section>

  <section aria-labelledby="verify">
    <h2 id="verify">Verification: what arrived equals what was sent</h2>
    <p>Filter the table by family. The pipeline checks count logs, spans and metric points exactly. The tracing checks confirm every trace has the exact span chain across both services. The HyperDX checks ask HyperDX's own API for the same numbers a user would see.</p>
    <div class="evid">
      {verify_table}
      <p class="src">Source: <code>stack/verify/verify.py</code>, run <code>{ver["run_id"]}</code>, captured {man["verify-report.json"]["captured_at"]}.</p>
    </div>
  </section>

  <section aria-labelledby="negative">
    <h2 id="negative">Negative tests: the checks can fail</h2>
    <p>A set of checks that has only ever passed proves little. The self-test corrupts the stored data in ClickHouse after a clean run, and requires the right checks to fail each time.</p>
    <div class="evid">
      <table class="data"><thead><tr><th scope="col">Corruption</th><th scope="col">Must fail</th><th scope="col">Failed</th></tr></thead><tbody>
{selftest_rows}
      </tbody></table>
      <p class="src">Source: <code>stack/verify/selftest.py</code>, captured {man["selftest.json"]["captured_at"]}. All {len(selft["mutations"])} corruptions were caught, and the clean baseline run had no failures.</p>
    </div>
  </section>

  <section aria-labelledby="chaos">
    <h2 id="chaos">Failure tests</h2>
    <p>Each scenario sends load, injects a failure part-way through, waits for the system to settle, and then runs the same count and duplicate checks.</p>
    <div class="evid">
      <table class="data"><thead><tr><th scope="col">Failure injected</th><th scope="col" class="num">Checks passed</th></tr></thead><tbody>
{chaos_rows}
      </tbody></table>
      <p class="src">Source: <code>stack/verify/chaos.py</code>, captured {man["chaos.json"]["captured_at"]}.</p>
    </div>
    <aside class="note"><p><strong>Replays depend on timing.</strong> Killing the ingestors only causes a replay if the kill lands between an insert and its acknowledgement. In the attempts captured for this course ClickHouse dropped {", ".join(attempts)} replayed inserts (the count varies from run to run). Every attempt ended with exact counts, so the result shows that nothing was lost or duplicated, and does not show that a replay happens every time.</p></aside>
  </section>

  <section aria-labelledby="latency">
    <h2 id="latency">Latency from event to queryable</h2>
    <p>For every row, latency is the time ClickHouse recorded the insert minus the time the event happened, so it covers the whole path. The collector flushes every 100 milliseconds in this stack, which is tuned for fast results. <a href="11-limits-tiers-and-aws.html">Module 11</a> shows what such a setting would cost on AWS.</p>
    {lat_table}
  </section>

  <section aria-labelledby="screens">
    <h2 id="screens">What a user sees</h2>
    <figure class="shot">
      <img src="../evidence/screens/dash-availability.png" alt="HyperDX dashboard named Availability and Latency, showing availability, requests, errors, latency by route and a per-route table">
      <figcaption>The availability and latency dashboard in HyperDX, captured from the live stack on {dash["captured_at"]}. The routes shown at 0% availability (<code>/error</code>, <code>/exception</code>, <code>/chain-fail</code>) fail by design. The one-hour window also contains many test runs, including the self-test runs that delete or alter rows on purpose, so a few errors on the chart are deliberate tampering and not platform failures.</figcaption>
    </figure>
    <figure class="shot">
      <img src="../evidence/screens/alerts.png" alt="HyperDX alerts page showing two alerts in the alert state, each configured as fires when the value is above 4">
      <figcaption>The two alerts in the ALERT state, captured on {alertshot["captured_at"]} after a test run. Both read "above (&gt;) 4": exactly four failures does not fire them.</figcaption>
    </figure>
  </section>

  <section aria-labelledby="limits">
    <h2 id="limits">What these results do not show</h2>
    <aside class="caveat"><p><strong>Open limits.</strong> The platform has not run on real AWS S3, and it has not been load-tested at production volume of about 1,100 events and 7,600 metric samples per second. The tests ran on one machine, natively on arm64; the verification was repeated on emulated amd64 before the checkout flow was added, and there has been no run on real x86 hardware. A crash of the collector loses what it holds in memory. These and the other limits are listed in module 11.</p></aside>
  </section>'''

q = quiz([
    {"q": f"All {ver['passed']} verification checks pass. What makes that result more than a coincidence?",
     "options": [
        ("The counts checked are large, so a pass would be very unlikely to happen by chance alone.", "Large counts do not show that a check can fail; revisit what the negative tests establish."),
        ("When the stored data is deliberately corrupted in a test, the matching checks fail, so a pass cannot come from checks that never fail.", "The self-test shows each check reacts to the wrong data, so a pass reflects the data being right."),
        ("HyperDX reported the same numbers from its own dashboards, which confirms the data is correct.", "HyperDX reads the same ClickHouse data, so agreement shows consistency across views and not that the data is correct."),
     ], "answer": 1},
    {"q": f"Three attempts at the crash scenario show {', '.join(attempts[:-1])} and {attempts[-1]} replayed inserts dropped, and all counts are exact. What is the correct conclusion?",
     "options": [
        ("Nothing was lost or duplicated, and whether one attempt shows a replay depends on timing.", "Exact counts after every attempt show correctness, and the replays show the dedup path is exercised when the timing allows."),
        ("The crash test is broken, because a kill during load should cause a replay in every single attempt.", "Whether a kill lands between an insert and its acknowledgement is timing-dependent, so zero in some attempts is expected."),
        ("Replays never happen, because the ingestors acknowledge every insert instantly after receiving it.", "The attempt with a dropped insert is direct evidence of a replay, so revisit the data-path figure."),
     ], "answer": 0},
    {"q": "Which claim do these results support?",
     "options": [
        ("The platform handles production load on AWS with no data loss and needs no further testing before launch.", "No run used AWS S3 or production volume, so that claim goes beyond the evidence."),
        ("A failure of the collector never loses data, because everything it holds is already in the Buffer.", "A collector crash loses what it holds in memory, which is why the collector is only restarted while idle in these tests."),
        ("The platform loses no data and creates no duplicates in the tested failure scenarios on one machine.", "That is exactly the scope of the failure tests, and it is stated with its limits."),
     ], "answer": 2},
], gate="All correct. You have finished the Story track. Continue to the track that matches your work: module 4 for developers, or module 7 for platform engineers.")

prev, nxt = neighbours("m03")
write("03-evidence-it-works.html", page(
    "03-evidence-it-works.html", "m03", "The evidence it works", "Story", "3 of 3",
    "What was tested, what the results were, and what they do not prove, with every number taken from the captured evidence.",
    ["read the headline results and say what each one proves,",
     "explain why negative tests matter for a test suite, and",
     "name what the results do not cover."],
    body, q, scripts=["sim-evidence.js"], prev=prev, next=nxt))
