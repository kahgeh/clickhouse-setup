#!/usr/bin/env python3
import ast, re
from lib import page, quiz, write, neighbours, evidence, esc

dl = evidence("alert-deliveries.json"); rep = evidence("alerts-report.json"); man = evidence("manifest.json"); ver = evidence("verify-report.json")
e16 = next(c for c in ver["checks"] if c["id"] == "E16")
figs = ast.literal_eval(re.search(r"measured (\{.*\})", e16["desc"]).group(1))

def pre(d, keep_lines=None):
    lines = d["body"].strip().split("\n")
    # drop the sample log lines that follow the code fence in the saved-search alert, keep the message
    if "```" in lines:
        lines = lines[:lines.index("```")]
    return f'title: {d["title"]}\nstate: {d["state"]}\nbody:\n' + "\n".join(lines)

fire_tile, fire_logs, ok_logs, ok_tile = dl[0], dl[1], dl[2], dl[3]
assert "60 exceeds 4" in fire_tile["body"] and "70 lines found" in fire_logs["body"]
assert fire_tile["state"] == "ALERT" and ok_tile["state"] == "OK"
lag = fire_tile["received_at"] - 1791021420  # the window ended at 9:57:00 UTC = 1791021420
assert 0 <= lag < 1, lag

rows = "\n".join(
    f'<tr class="{"is-ok" if c["ok"] else "is-bad"}"><td><code>{c["id"]}</code></td><td>{esc(c["desc"])}</td>'
    f'<td><span class="pill {"pill--ok" if c["ok"] else "pill--bad"}">{"pass" if c["ok"] else "fail"}</span></td></tr>' for c in rep["checks"])
shot = man["screens/alerts.png"]; rm = man["alerts-report.json"]; dm = man["alert-deliveries.json"]

body = f'''  <section aria-labelledby="two">
    <h2 id="two">The two alerts</h2>
    <p>This module explains when the platform's two alerts fire, why exactly four failures does not fire them, and what an alert evaluation cannot see. Both alerts watch for more than four failures in one minute. One counts failed requests on a dashboard tile and the other counts ERROR log lines in a saved search.</p>
    <ul>
      <li><strong>5xx errors above 4 per minute (dashboard tile)</strong> watches the tile <em>Errors (5xx)</em> of the Availability &amp; Latency dashboard, which counts server spans of <code>web</code> with error status (module 5).</li>
      <li><strong>ERROR logs above 4 per minute (saved search)</strong> watches the saved search <em>Error list: error logs</em>, which counts log lines with severity ERROR.</li>
    </ul>
    <p>The alerts are created from code through HyperDX's API when the stack is provisioned, like the dashboards. Both use a threshold of 4, the type <code>above_exclusive</code>, an interval of one minute, and one webhook channel that posts to a small local receiver. The receiver records each notification, which is how the tests check deliveries.</p>
    <figure class="shot">
      <img src="../evidence/screens/alerts.png" alt="HyperDX alerts page with two alerts in the ALERT state, each reading: if value is above (>) 4">
      <figcaption>The two alerts in the ALERT state, captured on {shot["captured_at"]}. Each reads "above (&gt;) 4".</figcaption>
    </figure>
  </section>

  <section aria-labelledby="semantics">
    <h2 id="semantics">Threshold semantics</h2>
    <p>HyperDX has two threshold types, and the names are easy to misread.</p>
    <table class="data">
      <thead><tr><th scope="col">Threshold type</th><th scope="col">Fires when</th><th scope="col">Message reads</th></tr></thead>
      <tbody>
        <tr><td><code>above</code></td><td>count &ge; threshold</td><td>"N meets or exceeds 4"</td></tr>
        <tr><td><code>above_exclusive</code></td><td>count &gt; threshold</td><td>"N exceeds 4"</td></tr>
      </tbody>
    </table>
    <p>The alerts here use <code>above_exclusive</code> and are named "above 4", so a count of exactly 4 does not fire them and a count of 5 does. The alerts page shows "above (&gt;) 4". A request that fails does not always add one to the count, because the two alerts count different things. A <code>/chain-fail</code> request is one 5xx server span, but it writes two ERROR log lines, one from each service, so the log alert counts it twice. Four <code>/chain-fail</code> requests are four failures for the tile and eight lines for the log alert.</p>
  </section>

  <section aria-labelledby="timing">
    <h2 id="timing">When the evaluation happens</h2>
    <p>HyperDX evaluates each alert once a minute, at second :00, over the minute that just ended. The window is a fixed minute, for example 9:56:00 to 9:57:00 UTC, and not a sliding one. The evaluation does not wait for slow data: a row counts only if it is already in ClickHouse at that moment, and a row that arrives after :00 is not counted by this evaluation or by any later one, because the next evaluation looks at the next minute.</p>
    <p>That is the late-row blind spot. The platform's measured delay from event to queryable, for every row of the verification run, was a median of {figs["logs"]["p50_s"]:.1f} s and a 99th percentile of {figs["logs"]["p99_s"]:.1f} s for logs, and a median of {figs["spans"]["p50_s"]:.1f} s and a 99th percentile of {figs["spans"]["p99_s"]:.1f} s for spans. A failure in the last few seconds of a minute can therefore be queryable only after :00. These numbers were measured on one laptop with a 100 ms collector flush; no measurement shows how often a real alert missed rows, and the simulator below takes the delay as an input.</p>
    <p>Two consequences follow. Failures spread across two minutes are counted separately, so three failures at the end of one minute and three at the start of the next are not six failures in one window. And the test that checks the boundary starts its burst early in a minute, between second 5 and second 40, so that every row has landed before :00.</p>
  </section>

  <section aria-labelledby="sim">
    <h2 id="sim">Alert window simulator</h2>
    <p>Set how many failures happen, the threshold and comparison, the second at which they happen and the ingest delay, and read whether the evaluation counts them. The timeline shows two minutes with the evaluation at second 60 and second 120.</p>
    <aside class="note"><p><strong>The model.</strong> All failures happen at one second, and they reach ClickHouse after the ingest delay. A row arriving at the evaluation second or later counts as late. That boundary is an assumption of the simulator, since sub-second timing was not measured. The comparison and the once-a-minute evaluation follow the behaviour described above.</p></aside>
    <div class="sim" id="alert-sim">
      <h3>Does the evaluation count them?</h3>
      <div class="controls">
        <label class="ctl">Failures <output id="al-fail-o">5</output>
          <input type="range" id="al-fail" min="0" max="100" value="5"></label>
        <label class="ctl">Threshold
          <input type="number" id="al-thr" min="1" max="100" value="4"></label>
        <label class="ctl">Comparison
          <select id="al-op"><option value="&gt;" selected>above (&gt;), above_exclusive</option><option value="&gt;=">above (&gt;=), meets or exceeds</option></select></label>
        <label class="ctl">Minute of the failures
          <select id="al-minute"><option value="first" selected>the first minute</option><option value="next">the next minute</option></select></label>
        <label class="ctl">Second within the minute <output id="al-sec-o">20 s</output>
          <input type="range" id="al-sec" min="0" max="59" value="20"></label>
        <label class="ctl">Ingest delay <output id="al-delay-o">1 s</output>
          <input type="range" id="al-delay" min="0" max="30" value="1"></label>
      </div>
      <figure>
        <svg id="alert-svg" viewBox="0 0 760 164" role="img" aria-labelledby="alert-t alert-d"><title id="alert-t">Timeline of two minutes with alert evaluations</title><desc id="alert-d">Two one-minute windows with an evaluation at the end of each. A marker shows when the failures happen and a second marker when they reach ClickHouse.</desc></svg>
        <figcaption>The failures happen at the first marker and reach ClickHouse at the second. A second marker after the evaluation line means the rows are late.</figcaption>
      </figure>
      <p class="out" id="al-out" aria-live="polite">Set the inputs to see the outcome.</p>
      <p class="readout" id="al-readout"></p>
    </div>
    <p>Try these. Set failures to 4 with the comparison "above (&gt;)": the count equals the threshold and nothing fires. Switch to "above (&gt;=)" and it fires. Set 20 failures at second 55 with a delay of 10 s: the rows arrive at second 65, after the evaluation, and nothing fires although 20 is above 4. Move the failures to "the next minute" to see the second evaluation count them.</p>
  </section>

  <section aria-labelledby="payloads">
    <h2 id="payloads">The notifications</h2>
    <p>When an alert changes to ALERT, HyperDX posts to the webhook, and it posts again when the alert returns to OK. The receiver stores the body, which is built from a template with the fields title, state, body and link. These are real deliveries recorded by the receiver during the alert tests. The sample log lines that the log alert appends to its message, and the links, are left out.</p>
    <div class="evid">
      <pre class="log">{esc(pre(fire_tile))}</pre>
      <pre class="log">{esc(pre(fire_logs))}</pre>
      <pre class="log">{esc(pre(ok_tile))}</pre>
      <p class="src">Source: webhook receiver, <code>{esc(dm["command"])}</code>, captured {dm["captured_at"]} (<code>evidence/alert-deliveries.json</code>). The counts match the checks below (60 failed requests and 70 ERROR lines). The firing notifications arrived {lag:.2f} s after the end of their window, and the resolve notifications one minute later.</p>
    </div>
    <p>The tile alert says "60 exceeds 4" and the log alert says "70 lines found, which exceeds the threshold of 4 lines". The log count is higher than the request count because requests to <code>/chain-fail</code> write two ERROR lines each.</p>
  </section>

  <section aria-labelledby="tests">
    <h2 id="tests">How the tests verified it</h2>
    <p>The alert test sends known traffic and reads the alert states from HyperDX and the notifications from the receiver. It waits for the evaluation, and it checks the boundary that matters: exactly four failures must not fire.</p>
    <div class="evid">
      <div class="alert-scroll">
      <table class="data">
        <thead><tr><th scope="col">Check</th><th scope="col">What it asserts</th><th scope="col">Result</th></tr></thead>
        <tbody>
{rows}
        </tbody>
      </table>
      </div>
      <p class="src">Source: <code>stack/verify/alerts.py</code>, run <code>{rep["run_id"]}</code>, captured {rm["captured_at"]}. {rep["passed"]} of {len(rep["checks"])} checks passed.</p>
    </div>
    <p>The boundary check is the one that can fail for the right reason. With the wrong threshold type, exactly four failures would send a notification and the check that no notification arrived would fail. The suite was also checked against deliberately wrong alert settings (unreachable thresholds and the <code>&gt;=</code> type), according to the project notes. Those runs are not part of the captured report, so this page does not show them.</p>
  </section>

  <section aria-labelledby="blind">
    <h2 id="blind">What an alert evaluation cannot see</h2>
    <ul>
      <li><strong>Rows that arrive after :00.</strong> They are not counted by any evaluation, so a burst at the end of a minute can pass unnoticed.</li>
      <li><strong>Failures split across windows.</strong> The windows are fixed minutes, so the count resets at every :00.</li>
      <li><strong>Anything the query does not count.</strong> The tile alert counts server spans of <code>web</code> with error status, and the log alert counts ERROR lines. A slow response with status 200 is neither.</li>
      <li><strong>A failed notification.</strong> The tests check that the receiver got the posts. They do not show what happens if a real destination is unreachable.</li>
    </ul>
    <aside class="caveat"><p><strong>Open limit.</strong> The blind spot for late rows is a property of how HyperDX evaluates, observed and documented in this project, and it is listed with the other limits in module 11. If alerts must not miss late data, the alert query or the window needs a delay allowance that this setup does not have.</p></aside>
  </section>'''

q = quiz([
    {"q": "In one minute two requests to <code>/chain-fail</code> and two to <code>/error</code> arrive, and nothing else fails. All of the data is in ClickHouse before :00. Which alerts fire?",
     "options": [
        ("Neither, because four failed requests equals the threshold of 4.", "The two alerts count different things, so check how many records one failing request produces for each."),
        ("Only the ERROR-log alert: six log lines against a threshold of 4.", "Six lines exceed 4, while four failed requests only equal it under the exclusive comparison."),
        ("Both, because each failing request adds one count to each of the two alerts.", "A request is not counted the same way by both alerts, so revisit what each alert's query counts."),
     ], "answer": 1},
    {"q": "A job fails 40 requests at second 57 of a minute, and the data is queryable at second 62. What do the alerts do?",
     "options": [
        ("They fire at the next evaluation, which includes the rows that arrived late in that minute.", "Each evaluation looks at the minute that just ended, so revisit which minute the late rows belong to."),
        ("They fire at :00, because 40 failures are far above the threshold of 4 whatever the delay.", "The count depends on which rows are in ClickHouse at :00, so look at when these arrive."),
        ("Neither fires for that minute, and the rows never show up in any alert's count.", "The evaluation at :00 saw none of them, and the next one covers a different minute."),
     ], "answer": 2},
    {"q": "Someone provisions an alert named \"above 4\" with the threshold type <code>above</code> and sends exactly four failures in one minute. What happens, and why?",
     "options": [
        ("It fires, because that type means count greater than or equal to the threshold.", "The type, not the name, sets the comparison, so exactly the threshold fires."),
        ("It stays quiet, because the name says above 4 and four is not above four.", "The type decides the comparison and the name does not, so revisit what each type means."),
        ("It fires only if the four failures are split over two one-minute windows.", "Windows are fixed minutes and the comparison applies to each one, so splitting does not change the rule."),
     ], "answer": 0},
], gate="All correct. You have finished the Using it track. Continue to module 7 if you also run the platform, or return to the course home.")

prev, nxt = neighbours("m06")
write("06-alerts.html", page(
    "06-alerts.html", "m06", "Alerts", "Using it", "3 of 3",
    "How the two alerts are evaluated, why exactly four failures does not fire them, and which failures an evaluation cannot see.",
    ["predict whether an alert fires for a given number of failures,",
     "explain why exactly the threshold does not fire, and",
     "describe what the alert evaluation cannot see."],
    body, q, scripts=["sim-alert.js"], prev=prev, next=nxt))
