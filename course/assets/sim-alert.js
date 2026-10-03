/* Module 6: alert window simulator. Pure functions first (tested by tools/test-sim-alert.js), DOM wiring below.
   Model, from the facts in the page: HyperDX evaluates once a minute at second :00 over the minute just ended,
   a row counts only if it is in ClickHouse by then, and a row that arrives later is not counted by any evaluation
   (the next evaluation looks at the next minute). The ingest delay is an input, not a measured value. */
var ALERT = (function () {
  var MINUTE = 60;
  function compare(count, threshold, op) { return op === ">=" ? count >= threshold : count > threshold; }
  // second: seconds from the start of the first minute shown (0..59 for the first minute, 60..119 for the next).
  function evaluate(p) {
    var failures = Math.max(0, Math.floor(p.failures)), second = p.second, delay = Math.max(0, p.delay);
    var win = Math.floor(second / MINUTE);                 // the minute the failures belong to
    var evalAt = (win + 1) * MINUTE;                       // second :00 that ends that minute
    var arrival = second + delay;                          // when the rows are in ClickHouse
    var late = arrival >= evalAt;                          // arriving at :00 or later is too late
    var counted = failures > 0 && !late ? failures : 0;
    var fires = compare(counted, p.threshold, p.op);
    var wouldFire = compare(failures, p.threshold, p.op);
    var outcome;
    if (failures === 0) outcome = "none";
    else if (late) outcome = "missed";
    else if (fires) outcome = win === 0 ? "fires" : "fires-next";
    else outcome = failures === p.threshold ? "no-fire-equal" : "no-fire-below";
    return { failures: failures, window: win, evalAt: evalAt, arrival: arrival, late: late, counted: counted,
      fires: fires, wouldFireIfCounted: wouldFire, outcome: outcome };
  }
  function opText(op) { return op === ">=" ? "at least" : "more than"; }
  function describe(p, r) {
    var T = p.threshold, win = r.window === 0 ? "the first minute" : "the next minute";
    switch (r.outcome) {
      case "none": return "No failures, so the count is 0 and nothing fires.";
      case "missed": return "Missed. The rows reach ClickHouse at second " + r.arrival + ", after the evaluation at second " + r.evalAt +
        ". That evaluation counts 0 for " + win + " and does not fire" + (r.wouldFireIfCounted ? ", although " + r.failures + " failures would have fired it if they had been counted" : "") +
        ". No later evaluation counts these rows, because the next one looks at the next minute.";
      case "fires": return "Fires. The evaluation at second " + r.evalAt + " counts " + r.counted + ", which is " + opText(p.op) + " " + T + ". A webhook is sent.";
      case "fires-next": return "Fires in the next window. The failures belong to the next minute, and the evaluation at second " + r.evalAt + " counts " + r.counted + ", which is " + opText(p.op) + " " + T + ". The first evaluation saw 0.";
      case "no-fire-equal": return "Does not fire. The evaluation at second " + r.evalAt + " counts " + r.counted + ", which equals the threshold. A comparison of '>' needs more than " + T + ".";
      default: return "Does not fire. The evaluation at second " + r.evalAt + " counts " + r.counted + ", below the threshold of " + T + ".";
    }
  }
  return { evaluate: evaluate, describe: describe, compare: compare };
})();

if (typeof module !== "undefined" && module.exports) module.exports = ALERT;

(function () {
  if (typeof document === "undefined") return;
  var svg = document.getElementById("alert-svg");
  if (!svg) return;
  var NS = "http://www.w3.org/2000/svg", X0 = 20, W = 720, SPAN = 150, K = W / SPAN;
  function $(id) { return document.getElementById(id); }
  function x(s) { return X0 + s * K; }
  function el(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function params() {
    var minute = $("al-minute").value === "next" ? 60 : 0;
    return { failures: +$("al-fail").value, threshold: Math.max(1, Math.floor(+$("al-thr").value) || 1), op: $("al-op").value,
      second: +$("al-sec").value + minute, delay: +$("al-delay").value };
  }
  function draw(p, r) {
    Array.prototype.slice.call(svg.childNodes).forEach(function (n) { if (n.nodeName !== "title" && n.nodeName !== "desc") svg.removeChild(n); });
    svg.appendChild(el("rect", { "class": "d-cell", x: x(0), y: 34, width: 60 * K, height: 60 }));
    svg.appendChild(el("rect", { "class": "d-cell", x: x(60), y: 34, width: 60 * K, height: 60 }));
    svg.appendChild(el("rect", { "class": "d-cell d-cell--idle", x: x(120), y: 34, width: 30 * K, height: 60 }));
    svg.appendChild(el("text", { "class": "d-label", x: x(2), y: 22 }, "Minute 1, evaluated at second 60"));
    svg.appendChild(el("text", { "class": "d-label", x: x(62), y: 22 }, "Minute 2, evaluated at second 120"));
    [60, 120].forEach(function (s) {
      svg.appendChild(el("line", { "class": "d-edge d-edge--live", x1: x(s), y1: 28, x2: x(s), y2: 100 }));
    });
    if (r.failures > 0) {
      var fx = x(p.second), ax = x(r.arrival);
      svg.appendChild(el("line", { "class": "d-edge", x1: fx, y1: 64, x2: ax, y2: 64 }));
      svg.appendChild(el("circle", { "class": "d-dot", cx: fx, cy: 64, r: 7 }));
      svg.appendChild(el("circle", { "class": r.late ? "d-dot d-dot--dup" : "d-dot", cx: ax, cy: 64, r: 7 }));
      svg.appendChild(el("text", { "class": "d-muted", x: Math.max(fx, X0 + 24), y: 116, "text-anchor": "middle" }, "failures"));
      if (r.arrival !== p.second) svg.appendChild(el("text", { "class": "d-muted", x: Math.min(ax, 740 - 50), y: 134, "text-anchor": "middle" }, r.late ? "in ClickHouse: late" : "in ClickHouse"));
    }
    svg.appendChild(el("line", { "class": "d-axis", x1: x(0), y1: 94, x2: x(150), y2: 94 }));
    [0, 30, 60, 90, 120, 150].forEach(function (s) { svg.appendChild(el("text", { "class": "d-tick", x: x(s), y: 154, "text-anchor": "middle" }, s + " s")); });
  }
  function update() {
    var p = params(), r = ALERT.evaluate(p);
    $("al-fail-o").textContent = p.failures;
    $("al-sec-o").textContent = $("al-sec").value + " s";
    $("al-delay-o").textContent = p.delay + " s";
    draw(p, r);
    $("al-out").textContent = ALERT.describe(p, r);
    $("al-readout").textContent = "failures " + r.failures + " at second " + p.second + ", in ClickHouse at second " + r.arrival +
      "; window " + (r.window + 1) + " is evaluated at second " + r.evalAt + "; counted " + r.counted + "; rule: count " + p.op + " " + p.threshold +
      "; result: " + r.outcome;
  }
  Array.prototype.forEach.call(document.querySelectorAll("#alert-sim input, #alert-sim select"), function (i) { i.addEventListener("input", update); i.addEventListener("change", update); });
  update();
})();
