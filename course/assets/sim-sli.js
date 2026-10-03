/* Module 5: SLI calculator. Pure functions first (tested with node), DOM wiring below.
   Availability = server spans of web with a non-5xx outcome / all server spans of web.
   Percentile = nearest rank over the weighted illustrative durations. */
var SLI = (function () {
  var ROUTES = [
    { id: "ok", status: 200 }, { id: "slow", status: 200 }, { id: "error", status: 500 },
    { id: "exception", status: 500 }, { id: "chain", status: 200 }, { id: "chain-fail", status: 502 }, { id: "checkout", status: 200 }
  ];
  function isFailure(status) { return status >= 500; }
  function clean(n) { n = Math.floor(Number(n)); return isFinite(n) && n > 0 ? n : 0; }
  function summarise(rows) {
    var total = 0, errors = 0, per = [];
    rows.forEach(function (r) {
      var c = clean(r.count), e = isFailure(r.status) ? c : 0;
      total += c; errors += e;
      per.push({ id: r.id, status: r.status, count: c, errors: e, availability: c ? (c - e) / c : null });
    });
    return { total: total, errors: errors, ok: total - errors, availability: total ? (total - errors) / total : null, perRoute: per };
  }
  // Nearest rank: sort all N durations ascending and take the value at position ceil(p/100 * N).
  function percentile(rows, p) {
    var items = rows.map(function (r) { return { ms: Number(r.ms), n: clean(r.count) }; })
      .filter(function (r) { return r.n > 0 && isFinite(r.ms); })
      .sort(function (a, b) { return a.ms - b.ms; });
    var N = items.reduce(function (s, r) { return s + r.n; }, 0);
    if (!N) return null;
    var rank = Math.max(1, Math.ceil(p / 100 * N - 1e-9)), seen = 0;
    for (var i = 0; i < items.length; i++) { seen += items[i].n; if (seen >= rank) return items[i].ms; }
    return items[items.length - 1].ms;
  }
  return { ROUTES: ROUTES, isFailure: isFailure, summarise: summarise, percentile: percentile };
})();

if (typeof module !== "undefined" && module.exports) module.exports = SLI;

(function () {
  if (typeof document === "undefined") return;
  var root = document.getElementById("sli-sim");
  if (!root) return;
  var expected = parseFloat(root.getAttribute("data-expected-availability"));
  var expectedTotal = parseInt(root.getAttribute("data-expected-total"), 10);
  var defaults = {};
  SLI.ROUTES.forEach(function (r) {
    var c = document.getElementById("sli-count-" + r.id);
    if (c) defaults[r.id] = c.value;
  });
  function rowsNow() {
    return SLI.ROUTES.map(function (r) {
      return { id: r.id, status: r.status, count: document.getElementById("sli-count-" + r.id).value, ms: document.getElementById("sli-ms-" + r.id).value };
    });
  }
  function pct(x) { return x == null ? "no data" : (x * 100).toFixed(2) + "%"; }
  function set(id, text) { var e = document.getElementById(id); if (e) e.textContent = text; }
  function update() {
    var rows = rowsNow(), s = SLI.summarise(rows);
    set("sli-total", String(s.total));
    set("sli-errors", String(s.errors));
    set("sli-avail", pct(s.availability));
    s.perRoute.forEach(function (r) {
      set("sli-r-err-" + r.id, String(r.errors));
      set("sli-r-av-" + r.id, pct(r.availability));
    });
    [50, 95, 99].forEach(function (p) {
      var v = SLI.percentile(rows, p);
      set("sli-p" + p, v == null ? "no data" : v + " ms");
    });
    set("sli-line", s.total
      ? s.ok + " non-5xx requests out of " + s.total + " is " + pct(s.availability) + ". " + s.errors + " failed with a 5xx status."
      : "No requests entered. A dashboard shows no data in that case, not 0% availability.");
    var same = SLI.ROUTES.every(function (r) { return String(clean(rows.filter(function (x) { return x.id === r.id; })[0].count)) === String(clean(defaults[r.id])); });
    var check = "";
    if (same && s.total === expectedTotal && s.availability != null) {
      var ok = Math.abs(s.availability - expected) < 1e-12;
      check = "Inputs equal the captured run: calculated " + s.availability.toFixed(6) + ", evidence expected_availability " + expected.toFixed(6) + (ok ? ". They match." : ". They do not match.");
    } else {
      check = "Inputs differ from the captured run, so there is nothing to compare with the evidence. Choose the captured run to compare.";
    }
    set("sli-check", check);
  }
  function clean(n) { n = Math.floor(Number(n)); return isFinite(n) && n > 0 ? n : 0; }
  // Presets (counts per route, in the order of SLI.ROUTES). "captured" uses the defaults read from the page.
  var PRESETS = {
    healthy: { ok: 150, slow: 20, error: 0, exception: 0, chain: 35, "chain-fail": 0, checkout: 10 },
    dependency: { ok: 100, slow: 20, error: 0, exception: 0, chain: 0, "chain-fail": 85, checkout: 0 }
  };
  var preset = document.getElementById("sli-preset");
  function applyCounts(map) {
    SLI.ROUTES.forEach(function (r) { document.getElementById("sli-count-" + r.id).value = map[r.id]; });
  }
  root.addEventListener("input", function (e) {
    if (preset && e.target !== preset && e.target.id && e.target.id.indexOf("sli-count-") === 0) preset.value = "custom";
    update();
  });
  if (preset) preset.addEventListener("change", function () {
    if (preset.value === "captured") applyCounts(defaults);
    else if (PRESETS[preset.value]) applyCounts(PRESETS[preset.value]);
    update();
  });
  update();
})();
