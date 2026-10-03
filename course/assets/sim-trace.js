/* Module 4: trace explorer. Data comes from the data-trace attribute of #trace-sim (written by tools/build-module04.py from
   evidence/trace-sample.json and the per-request contract). Bars show order and nesting only: the real durations are
   all about 6 ms, so bar length is illustrative and the real duration is printed as text. */
(function () {
  var NS = "http://www.w3.org/2000/svg";
  var dataEl = document.getElementById("trace-sim");
  var svg = document.getElementById("trace-svg");
  var out = document.getElementById("trace-out");
  if (!dataEl || !svg || !out) return;
  var DATA;
  try { DATA = JSON.parse(dataEl.getAttribute("data-trace")); } catch (e) { return; }

  var ROW = 44, TOP = 34, LEFT = 14, BAR_MAX = 440, STEP = 56, LABEL_X = 14;
  var mode = "chain", selected = 0;

  function depthOf(spans, i) {
    var d = 0, id = spans[i].ParentSpanId;
    while (id) {
      var p = -1;
      for (var k = 0; k < spans.length; k++) if (spans[k].SpanId === id) p = k;
      if (p < 0) break;
      d++; id = spans[p].ParentSpanId;
    }
    return d;
  }
  function el(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function current() { return mode === "chain" ? DATA.chain : DATA.fail; }
  function durText(s) { return s.duration_ms == null ? "duration not captured" : s.duration_ms + " ms"; }

  function draw() {
    var t = current(), spans = t.spans;
    Array.prototype.slice.call(svg.childNodes).forEach(function (n) { if (n.nodeName !== "title" && n.nodeName !== "desc") svg.removeChild(n); });
    svg.setAttribute("viewBox", "0 0 760 " + (TOP + spans.length * ROW + 10));
    svg.appendChild(el("text", { "class": "d-tick", x: LEFT, y: 18 }, "start"));
    svg.appendChild(el("text", { "class": "d-tick", x: 746, y: 18, "text-anchor": "end" }, "end (illustrative bar lengths)"));
    svg.appendChild(el("line", { "class": "d-axis", x1: LEFT, y1: 24, x2: 746, y2: 24 }));
    spans.forEach(function (s, i) {
      var d = spans[i].depth != null ? spans[i].depth : depthOf(spans, i), y = TOP + i * ROW;
      var g = el("g", { "data-span": i, tabindex: 0, role: "button", "aria-pressed": i === selected ? "true" : "false",
        "aria-label": s.ServiceName + " " + s.SpanKind + " " + s.SpanName + ", status " + s.StatusCode + ", " + durText(s) + ": show details" });
      g.appendChild(el("rect", { "class": i === selected ? "d-node d-node--busy" : "d-node", x: 4, y: y, width: 752, height: ROW - 6, rx: 4 }));
      g.appendChild(el("rect", { "class": s.StatusCode === "Error" ? "d-bar d-bar--warn" : "d-bar", x: LEFT + d * STEP, y: y + 6, width: BAR_MAX - d * STEP * 1.2, height: 12, rx: 2 }));
      g.appendChild(el("text", { "class": "d-label", x: LEFT + d * STEP, y: y + 31 },
        s.ServiceName + " " + s.SpanKind.toUpperCase() + "  " + s.SpanName));
      g.appendChild(el("text", { "class": "d-muted", x: 746, y: y + 18, "text-anchor": "end" },
        s.StatusCode + (s.duration_ms == null ? "" : "  " + s.duration_ms + " ms")));
      g.click = function () { select(i, true); };  // SVG elements have no click(); this lets scripts and tests activate a row
      g.addEventListener("click", function () { select(i, true); });
      g.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(i, true); }
        else if (e.key === "ArrowDown" && i < spans.length - 1) { e.preventDefault(); select(i + 1, true); }
        else if (e.key === "ArrowUp" && i > 0) { e.preventDefault(); select(i - 1, true); }
      });
      svg.appendChild(g);
    });
  }

  function line(label, value) {
    var p = document.createElement("p");
    var b = document.createElement("strong");
    b.textContent = label + " ";
    p.appendChild(b);
    p.appendChild(document.createTextNode(value));
    return p;
  }

  function detail() {
    var t = current(), s = t.spans[selected];
    while (out.firstChild) out.removeChild(out.firstChild);
    var h = document.createElement("h4");
    h.textContent = s.ServiceName + " " + s.SpanKind + " span: " + s.SpanName;
    out.appendChild(h);
    out.appendChild(line("Service:", s.ServiceName));
    out.appendChild(line("Kind:", s.SpanKind));
    out.appendChild(line("Status:", s.StatusCode));
    out.appendChild(line("Duration:", durText(s)));
    out.appendChild(line("Trace id:", t.trace_id));
    out.appendChild(line("Span id:", s.SpanId || "not captured (contract only)"));
    out.appendChild(line("Parent span id:", s.ParentSpanId ? s.ParentSpanId : (s.parent_note || "none: this is the root span")));
    var logs = t.logs.filter(function (l) { return s.SpanId ? l.SpanId === s.SpanId : l.ServiceName === s.ServiceName && l.for_kind === s.SpanKind; });
    var hl = document.createElement("p");
    hl.className = "tag";
    hl.textContent = logs.length ? "Log lines carrying this span's id (" + logs.length + "):" : "No log line carries this span's id.";
    out.appendChild(hl);
    logs.forEach(function (l) {
      var pre = document.createElement("pre");
      pre.className = "log";
      pre.textContent = l.SeverityText + "  " + l.ServiceName + "  " + (l.Body || "(body not captured)") + "  trace=" + (mode === "chain" ? t.trace_id.slice(0, 8) + ".." : "n/a") + "  span=" + (l.SpanId || "n/a");
      out.appendChild(pre);
    });
    var src = document.getElementById("trace-src");
    if (src) src.textContent = t.label;
  }

  function select(i, focus) {
    if (i >= current().spans.length) return;
    selected = i;
    draw(); detail();
    if (focus) { var g = svg.querySelector('[data-span="' + i + '"]'); if (g) g.focus(); }
  }
  function setMode(m) {
    mode = m; selected = 0;
    Array.prototype.forEach.call(document.querySelectorAll("[data-trace-mode]"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-trace-mode") === m ? "true" : "false");
    });
    draw(); detail();
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-trace-mode]"), function (b) {
    b.addEventListener("click", function () { setMode(b.getAttribute("data-trace-mode")); });
  });
  setMode("chain");
})();
