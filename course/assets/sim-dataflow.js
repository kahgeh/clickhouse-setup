/* Module 9: data-flow explorer with fault toggles. Pure model first (testable with node), DOM wiring last.
   One tick is one second. The rates are illustrative; the behaviour follows the facts documented for the stack:
   - the collector holds a batch in memory until the Buffer write succeeds, and retries a failed write for up to 5 minutes;
   - an ingestor acknowledges a range only after ClickHouse accepts it, so a kill between the two replays the range;
   - ClickHouse drops a replayed insert because the token was already accepted.
   Beyond the tested range (outages longer than 30 s) the model is an extrapolation of the documented retry window. */
(function (root) {
  "use strict";
  var EMIT = 10;            // rows the applications produce per second
  var INGEST_ENTRIES = 3;   // queue entries one ingestor processes per second
  var RETRY_WINDOW = 300;   // seconds the collector retries a failed Buffer write (default 5 minutes)

  function clone(s) { return JSON.parse(JSON.stringify(s)); }

  function create() {
    return { t: 0, emitted: 0, pending: [], buffer: [], tokens: [], written: 0, clickhouse: 0, replayed: 0, dropped: 0, lost: 0, nextId: 1,
             up: { traffic: true, s3: true, ingestor: true, clickhouse: true }, armedKill: false, notes: [] };
  }

  function pendingRows(s) { return s.pending.reduce(function (a, b) { return a + b.rows; }, 0); }
  function queueRows(s) { return s.buffer.reduce(function (a, b) { return a + b.rows; }, 0); }
  function waitingRows(s) { return s.buffer.reduce(function (a, b) { return a + (b.inserted ? 0 : b.rows); }, 0); }

  function accounting(s) {
    var p = pendingRows(s), w = waitingRows(s);
    return { emitted: s.emitted, inCollector: p, queueDepth: queueRows(s), queueWaiting: w, written: s.written, inClickHouse: s.clickhouse,
             replayed: s.replayed, dropped: s.dropped, lost: s.lost,
             unaccounted: s.emitted - s.lost - p - w - s.clickhouse };
  }

  // One second of simulated time.
  function tick(s) {
    var n = s;
    // 1. the collector tries to write each in-memory batch to the Buffer
    var keep = [];
    n.pending.forEach(function (b) {
      if (n.up.s3) { n.buffer.push({ id: n.nextId++, rows: b.rows, inserted: false }); n.written += b.rows; }
      else {
        b.age += 1;
        if (b.age > RETRY_WINDOW) { n.lost += b.rows; n.notes.push("A batch of " + b.rows + " rows exceeded the retry window and was dropped."); }
        else keep.push(b);
      }
    });
    n.pending = keep;
    // 2. the ingestor reads the queue (needs S3), inserts into ClickHouse, then acknowledges
    if (n.up.ingestor && n.up.s3 && n.up.clickhouse) {
      var done = 0;
      while (done < INGEST_ENTRIES && n.buffer.length) {
        var e = n.buffer[0];
        if (n.tokens.indexOf(e.id) !== -1) {
          n.dropped += e.rows; n.replayed += e.rows;
          n.notes.push("Replay of queue entry " + e.id + ": ClickHouse dropped " + e.rows + " rows because the token was already accepted.");
        } else { n.tokens.push(e.id); n.clickhouse += e.rows; e.inserted = true; }
        if (n.armedKill) {
          n.armedKill = false; n.up.ingestor = false;
          n.notes.push("The ingestor was killed after inserting entry " + e.id + " and before acknowledging it.");
          break;
        }
        n.buffer.shift(); done += 1;
      }
    }
    // 3. the applications emit this second's rows into the collector
    if (n.up.traffic) { n.pending.push({ rows: EMIT, age: 0 }); n.emitted += EMIT; }
    return n;
  }

  function step(state, seconds) {
    var s = clone(state); s.notes = [];
    for (var i = 0; i < seconds; i++) { s.t += 1; tick(s); }
    return s;
  }

  function setUp(state, which, up) {
    var s = clone(state); s.notes = [];
    s.up[which] = up;
    return s;
  }

  function killMidCommit(state) {
    var s = clone(state); s.notes = [];
    if (!s.up.ingestor) { s.notes.push("The ingestor is already stopped."); return s; }
    s.armedKill = true;
    s.notes.push("Armed: the ingestor dies right after its next insert, before it acknowledges.");
    return s;
  }

  function crashCollector(state) {
    var s = clone(state); s.notes = [];
    var p = pendingRows(s);
    s.lost += p; s.pending = [];
    s.notes.push("The collector crashed and restarted. The " + p + " rows in its memory were lost. Everything already in the Buffer is safe.");
    return s;
  }

  // Test helper: stop traffic and run until the pipeline is empty (or give up).
  function drain(state, maxSeconds) {
    var s = setUp(state, "traffic", false);
    for (var i = 0; i < (maxSeconds || 2000); i++) {
      if (!s.pending.length && !s.buffer.length) break;
      s = step(s, 1);
    }
    return s;
  }

  var api = { EMIT: EMIT, RETRY_WINDOW: RETRY_WINDOW, create: create, step: step, setUp: setUp, killMidCommit: killMidCommit,
              crashCollector: crashCollector, accounting: accounting, drain: drain };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof document === "undefined") return;

  // ---------------- DOM ----------------
  var panel = document.getElementById("flow-panel");
  if (!panel) return;
  var state = create();
  var $ = function (id) { return document.getElementById(id); };
  var history = $("flow-history"), summary = $("flow-summary"), notes = $("flow-notes");
  var NODE_UP = { s3: "flow-buffer", ingestor: "flow-ingestor", clickhouse: "flow-clickhouse" };

  function label(a, which, onWord, offWord) { return which ? onWord : offWord; }
  function render(action) {
    var a = accounting(state);
    $("flow-t-apps").textContent = String(a.emitted);
    $("flow-t-collector").textContent = String(a.inCollector);
    $("flow-t-buffer").textContent = String(a.queueDepth);
    $("flow-t-written").textContent = a.written + " written in total";
    $("flow-t-ingestor").textContent = state.up.ingestor ? (state.armedKill ? "armed to die" : "running") : "stopped";
    $("flow-t-clickhouse").textContent = String(a.inClickHouse);
    Object.keys(NODE_UP).forEach(function (k) {
      var down = !state.up[k];
      $(NODE_UP[k]).setAttribute("class", "d-node" + (down ? " d-node--down" : ""));
      var btn = $("flow-b-" + k);
      btn.setAttribute("aria-pressed", down ? "true" : "false");
      btn.textContent = (down ? "Start " : "Stop ") + { s3: "S3", ingestor: "ingestor", clickhouse: "ClickHouse" }[k];
    });
    var tr = $("flow-b-traffic");
    tr.setAttribute("aria-pressed", state.up.traffic ? "false" : "true");
    tr.textContent = state.up.traffic ? "Stop traffic" : "Start traffic";
    $("flow-edge-live").setAttribute("class", "d-edge" + (state.up.traffic ? " d-edge--live" : ""));
    var safe = a.lost === 0;
    summary.innerHTML = "Time <strong>" + state.t + " s</strong>. Produced " + a.emitted + " rows. In collector memory " + a.inCollector +
      ". Written to the Buffer so far " + a.written + ". Queue depth now " + a.queueDepth + ". In ClickHouse " + a.inClickHouse + ". Replayed rows dropped as duplicates " + a.dropped +
      ". <strong>" + (safe ? "Nothing lost so far." : a.lost + " rows lost.") + "</strong> Unaccounted rows: " + a.unaccounted + ".";
    notes.textContent = state.notes.length ? state.notes.slice(0, 4).join(" ") : "";
    if (action) {
      var row = document.createElement("tr");
      if (state.notes.length) row.className = "changed";
      var cells = [state.t, action, a.inCollector, a.queueDepth, a.inClickHouse, a.dropped, a.lost];
      row.innerHTML = cells.map(function (c, i) { return (i === 0 ? "<th scope=\"row\">" : "<td>") + c + (i === 0 ? "</th>" : "</td>"); }).join("");
      history.appendChild(row);
      while (history.children.length > 14) history.removeChild(history.firstChild);
    }
  }
  function act(fn, text) { state = fn(state); render(text); }

  ["s3", "ingestor", "clickhouse", "traffic"].forEach(function (k) {
    $("flow-b-" + k).addEventListener("click", function () {
      var nowUp = !state.up[k];
      var names = { s3: "S3", ingestor: "Ingestor", clickhouse: "ClickHouse", traffic: "Traffic" };
      act(function (s) { return setUp(s, k, nowUp); }, names[k] + (nowUp ? " started" : " stopped"));
    });
  });
  $("flow-b-kill").addEventListener("click", function () { act(killMidCommit, "Kill ingestor mid-commit (armed)"); });
  $("flow-b-crash").addEventListener("click", function () { act(crashCollector, "Collector crash"); });
  [1, 10, 60].forEach(function (n) {
    $("flow-b-step" + n).addEventListener("click", function () { act(function (s) { return step(s, n); }, "Step " + n + " s"); });
  });
  $("flow-b-reset").addEventListener("click", function () { state = create(); history.innerHTML = ""; render("Reset"); });
  render("");
})(typeof window !== "undefined" ? window : this);
