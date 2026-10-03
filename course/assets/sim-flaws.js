/* Module 10: spot-the-flaw exercise. The scenarios are data; evaluate() is a pure function (testable with node).
   The page renders the exercise into #flaw-root. Without JavaScript the lessons table on the page lists the same checks. */
(function (root) {
  "use strict";
  // verdict: "yes" the check would have caught the flaw, "partly" it would show a symptom or reduce the risk, "no" it would not.
  var SCENARIOS = [
    { id: "traces", title: "The traces claim",
      text: "The vendor documentation lists traces as a supported signal for the object-storage exporter. You plan to send traces through it.",
      checks: [
        { t: "Re-read the documentation page and the release notes", v: "no", why: "Documentation is the claim itself. Re-reading it cannot show whether the shipped binary matches it." },
        { t: "Run the exact image you will ship with a traces pipeline configured and validate it", v: "yes", why: "The image refused the configuration with 'telemetry type is not supported'. Running the artifact tests the claim." },
        { t: "Ask other users whether traces work", v: "partly", why: "Another user's experience may expose the gap, but it is second-hand and may refer to a different version." },
        { t: "Check that the documentation version number matches the image tag", v: "no", why: "A matching number shows the documentation is current, which says nothing about whether it is accurate." }
      ] },
    { id: "retry", title: "Data dropped in an outage",
      text: "The collector exports batches to the object store. In healthy conditions every count is exact, and you want to trust the delivery path.",
      checks: [
        { t: "Run the exact-count checks on a healthy system under load", v: "no", why: "Nothing fails while the object store is up. The missing retry only shows when a write fails." },
        { t: "Stop the object store for several seconds during load and compare counts with the ground truth", v: "yes", why: "The unpatched exporter logged 'Exporting failed. Dropping data' and the counts came up short. Injecting the fault early exposes it." },
        { t: "Read the exporter's description of durability", v: "no", why: "A description of intent does not show what happens when a write fails." },
        { t: "Stop the object store for 3 seconds only", v: "partly", why: "A short stop can be absorbed by the producer's own retries inside the call, so it can pass while a longer one loses data." }
      ] },
    { id: "boundary", title: "The alert threshold",
      text: "An alert is set to fire when failures are 'above 4' in a minute. You test it by sending 60 failing requests, and it fires.",
      checks: [
        { t: "Send exactly 4 failures and require that the alert stays quiet", v: "yes", why: "HyperDX's 'above' means 4 or more, so exactly 4 fired it. The boundary probe distinguishes above from above-exclusive." },
        { t: "Send 600 failures and require that it fires", v: "no", why: "Any comparison fires for a large burst, so a bigger burst tells you nothing new about the boundary." },
        { t: "Send no failures and require that it stays quiet", v: "no", why: "Zero is below the threshold under either comparison." },
        { t: "Read the label 'above' in the interface", v: "no", why: "The label is what suggested a strict comparison in the first place." }
      ] },
    { id: "cannot-fail", title: "A suite that cannot fail",
      text: "All 31 checks pass on every run. You want to know whether they would notice a real fault.",
      checks: [
        { t: "Run the suite again several times", v: "no", why: "Repeating a passing run does not show that any check can fail." },
        { t: "Corrupt the stored data on purpose and require the matching checks to fail", v: "yes", why: "The self-test deletes, flips, duplicates, re-parents and inflates data and requires a failure each time. It also exposed bugs in the verifier." },
        { t: "Add more checks of the same kind", v: "no", why: "More checks that are never shown to fail add more unproven checks." },
        { t: "Review the check code by reading it", v: "partly", why: "Review can find mistakes, but the early exit, the masked crash and the reused run id in the verifier were found by running the suite against damaged data." }
      ] },
    { id: "run-id", title: "The reused run id",
      text: "Each load run is tagged with a run id, and the checks count rows by that id. A run is started with an id used before.",
      checks: [
        { t: "Make the test refuse to start if rows for that run id already exist", v: "yes", why: "A refused start names the cause directly, before any count is wrong." },
        { t: "Compare counts with the ground truth after the run", v: "partly", why: "The counts would be wrong and the check would fail, but it points at the pipeline and not at the reused id." },
        { t: "Trust the count check because it passed last time", v: "no", why: "A pass on a different run says nothing about this run's precondition." },
        { t: "Look at the dashboard for the run", v: "no", why: "A dashboard filtered by the id shows the inflated numbers without saying they are wrong." }
      ] },
    { id: "registry", title: "The image that disappeared",
      text: "The local object store runs from a public registry image. The registry removes those images, and a new machine cannot pull it.",
      checks: [
        { t: "Mirror the pinned image in your own registry", v: "yes", why: "A copy you control still exists when the public one is deleted." },
        { t: "Pin the image to a tag and rely on the public registry", v: "partly", why: "A pinned tag records what you used, but it cannot be pulled once the registry deletes it." },
        { t: "Use the latest tag so you always get a current image", v: "no", why: "It does not help when the image no longer exists, and it removes reproducibility." },
        { t: "Keep a tested fallback: a second S3-compatible store that supports conditional writes", v: "yes", why: "The replacement had to support conditional writes for the manifest. A tested fallback shows that before you need it." }
      ] },
    { id: "empty-buckets", title: "The empty time buckets",
      text: "A dashboard availability tile uses a query whose numbers check out against the ground truth. The chart is shown to users.",
      checks: [
        { t: "Open the rendered dashboard and look at it", v: "yes", why: "Looking showed time buckets with no requests drawn as 0% availability. The query numbers were right and the picture was wrong." },
        { t: "Verify the same query through the API and compare numbers", v: "no", why: "The numbers were already correct. The flaw was in how empty buckets were drawn." },
        { t: "Add a unit test for the availability formula", v: "no", why: "The formula was correct, so the test passes while the chart misleads." },
        { t: "Count the tiles on the dashboard", v: "no", why: "Tiles can all exist and still draw misleading charts." }
      ] }
  ];

  var LABEL = { yes: "Would have caught it", partly: "Partly: shows a symptom or reduces the risk", no: "Would not have caught it" };

  function evaluate(id, index) {
    var s = SCENARIOS.filter(function (x) { return x.id === id; })[0];
    if (!s || !s.checks[index]) return null;
    var c = s.checks[index];
    return { verdict: c.v, label: LABEL[c.v], why: c.why,
             all: s.checks.map(function (k) { return { t: k.t, verdict: k.v, label: LABEL[k.v] }; }) };
  }

  var api = { SCENARIOS: SCENARIOS, LABEL: LABEL, evaluate: evaluate };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof document === "undefined") return;
  var host = document.getElementById("flaw-root");
  if (!host) return;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  host.textContent = "";
  SCENARIOS.forEach(function (s, si) {
    var fs = el("fieldset", "flaw-q");
    fs.appendChild(el("legend", "", (si + 1) + ". " + s.title));
    fs.appendChild(el("p", "", s.text));
    fs.appendChild(el("p", "tag", "What would you check?"));
    var result = el("div", "flaw-result");
    result.setAttribute("aria-live", "polite");
    s.checks.forEach(function (c, ci) {
      var lab = el("label", "flaw-opt");
      var inp = document.createElement("input");
      inp.type = "radio"; inp.name = "flaw-" + s.id; inp.value = String(ci);
      inp.addEventListener("change", function () {
        var r = evaluate(s.id, ci);
        result.textContent = "";
        var head = el("p", "");
        head.appendChild(el("span", "pill " + (r.verdict === "yes" ? "pill--ok" : r.verdict === "no" ? "pill--bad" : ""), r.label));
        head.appendChild(document.createTextNode(" " + r.why));
        result.appendChild(head);
        var ul = el("ul", "flaw-all");
        r.all.forEach(function (a) { ul.appendChild(el("li", "", a.label + ": " + a.t)); });
        result.appendChild(el("p", "tag", "All four checks:"));
        result.appendChild(ul);
      });
      lab.appendChild(inp);
      lab.appendChild(document.createTextNode(" " + c.t));
      fs.appendChild(lab);
    });
    fs.appendChild(result);
    host.appendChild(fs);
  });
})(typeof window !== "undefined" ? window : this);
