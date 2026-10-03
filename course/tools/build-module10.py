#!/usr/bin/env python3
from lib import page, quiz, write, neighbours, evidence, esc

man = evidence("manifest.json")
traces_txt = evidence("war-traces-unsupported.txt").rstrip()
retry_txt = evidence("war-no-retry.txt").rstrip()
n_drop = retry_txt.count("Dropping data")
assert n_drop == man["war-no-retry.txt"]["dropping_lines"] == 3

body = f'''  <section aria-labelledby="stories">
    <h2 id="stories">War stories</h2>
    <p>Seven things went wrong while the platform was built, and most were found by a deliberate check and not by luck. Every story below gives what happened, the evidence where there is a captured one, and the lesson. The two log excerpts were reproduced from the unpatched upstream collector image and were captured on {man["war-traces-unsupported.txt"]["captured_at"][:10]}.</p>

    <h3 id="s1">1. The documentation said traces were supported</h3>
    <p>The vendor's documentation described traces as a supported signal for the object-storage exporter. The shipped collector image rejected a traces pipeline when it was validated:</p>
    <div class="evid">
<pre class="log"><code>{esc(traces_txt)}</code></pre>
      <p class="src">Source: <code>evidence/war-traces-unsupported.txt</code>, upstream image <code>ghcr.io/opendata-oss/otel-collector:v0.4.0</code>. The traces signal was then added in a local patch to the exporter.</p>
    </div>
    <p><strong>Lesson.</strong> Verify a claim against the artifact you will run. Documentation, release notes and descriptions state intent, and only running the image shows what it does.</p>

    <h3 id="s2">2. Data dropped when the object store stopped</h3>
    <p>The upstream exporter had no retry. While the object store was stopped for about 9 seconds during a send of 300 log records, each failed write was logged and the batch was discarded:</p>
    <div class="evid">
<pre class="log"><code>{esc(retry_txt)}</code></pre>
      <p class="src">Source: <code>evidence/war-no-retry.txt</code>, same upstream image. {n_drop} batches of 100 items were dropped, which is the 300 records. The long storage-error lines are cut off in the capture.</p>
    </div>
    <p>The exporter's per-call ceiling is 5 seconds by default, and crossing it cancels the call and drops the batch. The producer underneath retries inside that call, so a short outage can be absorbed, and a stop of 9 seconds was not. The 9-second capture is the evidence kept here. A local patch adds <code>retry_on_failure</code>, on by default, and the failure tests now pass with the object store stopped for 8 s and for 30 s under load.</p>
    <p><strong>Lesson.</strong> Inject faults early. A system that passes while everything is healthy has not yet been asked to do the thing it exists for, and a short fault can hide a gap that a longer one exposes.</p>

    <h3 id="s3">3. "Above" meant "at least"</h3>
    <p>The alerts were set to fire above 4 failures a minute. HyperDX's <code>above</code> means greater than or equal to, so exactly 4 failures fired it, and <code>above_exclusive</code> means greater than. The boundary probe in <code>alerts.py</code> sends exactly 4 failures and requires the alerts to stay quiet. The alerts now use the exclusive form, and a negative test with the old comparison makes the probe fail. The firing messages differ: "N exceeds 4" for exclusive, "N meets or exceeds 4" for inclusive.</p>
    <p><strong>Lesson.</strong> Test the boundary. A burst far above a threshold passes under any comparison, and only the value on the threshold tells them apart.</p>

    <h3 id="s4">4. A test suite that could not fail</h3>
    <p>A suite of passing checks had never been shown to detect a fault. The self-test damages stored data in {len(evidence("selftest.json")["mutations"])} ways and requires the right checks to fail each time. It found three defects in the verifier: an early exit that hid other checks, a crash masked by a stale report, and a reused run id (next story). Module 9 shows the results.</p>
    <p><strong>Lesson.</strong> Test the tests with negative cases, and treat a suite that has only ever passed as unproven.</p>

    <h3 id="s5">5. Reused run ids tripled the counts</h3>
    <p>Checks count rows by run id. Reusing an id meant earlier data was counted again, and every count silently tripled. <code>verify.py</code> now checks first and exits with a message that the run id already has spans and that run ids must be unique, so the cause is named before any count is compared.</p>
    <p><strong>Lesson.</strong> Make tests refuse unsafe preconditions. A wrong count that looks like a pipeline fault wastes the time of whoever investigates it.</p>

    <h3 id="s6">6. The image registry removed the object store</h3>
    <p>The first local object store was MinIO. Its public images were removed in 2026, and the stack moved to RustFS, which also provides the S3 conditional writes that the Buffer's manifest depends on. The compose file records the reason next to the image.</p>
    <p><strong>Lesson.</strong> Pin what you depend on, keep a copy you control, and test a fallback that meets the same requirements before you need it.</p>

    <h3 id="s7">7. The dashboard drew empty buckets as 0% availability</h3>
    <p>Every number behind the availability tile was verified through HyperDX's API. Rendering the dashboard in headless Chrome and looking at it showed time buckets with no requests drawn as 0% availability. The query was right and the picture was wrong. The fix was <code>fillNulls: false</code> on the tile, and only the main dashboard has been inspected this way so far.</p>
    <p><strong>Lesson.</strong> Look at the rendered result as well as the numbers. A correct query can still draw something misleading.</p>
  </section>

  <section aria-labelledby="lessons">
    <h2 id="lessons">Lessons</h2>
    <table class="data">
      <thead><tr><th scope="col">Story</th><th scope="col">Flaw</th><th scope="col">Check that would catch it</th></tr></thead>
      <tbody>
        <tr><td>Traces claim</td><td>Documentation stated more than the artifact did</td><td>Run the shipped image with the configuration you need</td></tr>
        <tr><td>No retry</td><td>A failed write dropped the batch</td><td>Stop the dependency during load and compare counts with ground truth</td></tr>
        <tr><td>Alert threshold</td><td><code>above</code> meant at least</td><td>Send exactly the threshold value and require silence</td></tr>
        <tr><td>Suite that could not fail</td><td>Checks never shown to detect a fault</td><td>Corrupt data on purpose and require a failure</td></tr>
        <tr><td>Reused run id</td><td>Earlier data counted again</td><td>Refuse to start when the precondition is unsafe</td></tr>
        <tr><td>Removed image</td><td>A dependency disappeared from the registry</td><td>Pin, mirror and keep a tested fallback</td></tr>
        <tr><td>Empty buckets</td><td>A correct query drew a misleading chart</td><td>Look at the rendered result</td></tr>
      </tbody>
    </table>
    <aside class="note"><p><strong>The pattern.</strong> Several of these were found by a check designed to fail: a fault injection, a boundary value, a corrupted dataset or a closer look at a rendered chart. The exception is the removed image, which was found when a pull failed.</p></aside>
  </section>

  <section aria-labelledby="exercise">
    <h2 id="exercise">Spot the flaw</h2>
    <p>Each scenario below is one of the stories. Choose a check and see whether it would have caught the flaw, and why. Choosing one check shows the verdict on all four. The exercise needs JavaScript, and the lessons table above lists the same checks.</p>
    <div class="sim" id="flaw-panel">
      <div id="flaw-root"><p>The exercise loads here when JavaScript is available.</p></div>
    </div>
  </section>'''

q = quiz([
    {"q": "A team adopts a queue component whose documentation says it retries failed writes. Before relying on it, what produces the strongest evidence?",
     "options": [
        ("Read the documentation section on retries in detail, and check that its version matches the one you install.", "Documentation states what the authors intend, and the traces story shows it can be ahead of the shipped artifact. Revisit what a claim is evidence of."),
        ("Run the exact-count checks on a healthy system several times and compare the totals each time.", "Nothing fails while storage is up, so a missing retry would not show. Revisit the no-retry story."),
        ("Stop its storage past the call ceiling during load and compare what arrived with what was sent.", "A failed write is what retry exists for, so injecting it under load tests the claim on the artifact."),
     ], "answer": 2},
    {"q": "An alert is meant to fire when there are more than 4 failures a minute. A test sends 60 failures and the alert fires. Which additional test would reveal that the rule actually fires at 4 or more?",
     "options": [
        ("Send 6 failures and require the alert to fire within the minute.", "Six is above the threshold under either comparison, so it passes either way. Revisit the boundary story."),
        ("Send exactly 4 failures and require that the alert does not fire.", "Only the value on the threshold separates greater-than from at-least."),
        ("Send 600 failures and check the count printed in the message.", "A large burst fires under any comparison, and the message number only reports the count."),
     ], "answer": 1},
    {"q": "A dashboard tile returns the correct availability for every minute that has traffic, yet users see occasional 0% dips between bursts. What would find the cause?",
     "options": [
        ("Opening the rendered dashboard and checking the minutes with no traffic.", "The numbers were correct and the flaw was in how empty buckets were drawn, which only the rendered chart shows."),
        ("Re-running the API checks that compare the counts with the ground truth.", "Those counts were already right, so they pass. Revisit what each kind of check can see."),
        ("Verifying the availability formula with a unit test for each of the three status classes.", "The formula is correct for the minutes with traffic, so the test passes while the chart still misleads."),
     ], "answer": 0},
], gate="All correct. Continue to module 11, limits, tiers and the road to AWS.")

prev, nxt = neighbours("m10")
write("10-what-broke.html", page(
    "10-what-broke.html", "m10", "What broke and what we learned", "Running it", "4 of 5",
    "Seven failures found while building the platform, the evidence for each, and the check that would have caught it sooner.",
    ["recognise the failure patterns found while building,",
     "apply the checks that would have caught them, and",
     "separate vendor claims from verified behaviour."],
    body, q, scripts=["sim-flaws.js"], mermaid=False, prev=prev, next=nxt))
