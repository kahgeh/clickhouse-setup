/* Module 7: idempotency lab. Pure functions first (testable with node), DOM wiring last.
   Model of the real stack: a table with a ClickHouse insert-deduplication window keyed by token, and a
   ReplacingMergeTree whose sort key contains the provenance key (sequence, entry, record). */
(function (root) {
  "use strict";
  var WINDOW = 1000; // non_replicated_deduplication_window

  // One chunk is two log rows read from the queue at sequence 17. Each row has a provenance key.
  var CHUNK = [
    { key: "17/0/0", body: "GET /ok 200" },
    { key: "17/0/1", body: "GET /error 500" }
  ];

  function createState() { return { rows: [], tokens: [], nextId: 1, log: [] }; }

  function clone(s) { return JSON.parse(JSON.stringify(s)); }

  // Insert a chunk with an insert token. Same token inside the window: dropped, nothing stored.
  function insert(state, token, chunk) {
    var s = clone(state);
    if (s.tokens.indexOf(token) !== -1) {
      return { state: s, accepted: false, added: 0,
        message: "Dropped as a duplicate: ClickHouse has already accepted token " + token + ", so the insert did nothing." };
    }
    chunk.forEach(function (r) { s.rows.push({ key: r.key, body: r.body, token: token, id: s.nextId++ }); });
    s.tokens.push(token);
    if (s.tokens.length > WINDOW) s.tokens.shift();
    return { state: s, accepted: true, added: chunk.length,
      message: "Accepted: token " + token + " is new, so " + chunk.length + " rows were stored." };
  }

  // FINAL view: rows with the same sort key collapse to one (the same adapter version, so the first stays).
  function finalView(state) {
    var seen = {}, out = [];
    state.rows.forEach(function (r) { if (!seen[r.key]) { seen[r.key] = true; out.push(r); } });
    return out;
  }

  // A background merge physically removes the collapsed rows.
  function merge(state) {
    var s = clone(state);
    var before = s.rows.length;
    s.rows = finalView(s);
    return { state: s, removed: before - s.rows.length };
  }

  function rawCount(state) { return state.rows.length; }
  function distinctKeys(state) { return finalView(state).length; }

  var api = { CHUNK: CHUNK, createState: createState, insert: insert, finalView: finalView, merge: merge,
              rawCount: rawCount, distinctKeys: distinctKeys };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.IdempotencyLab = api;

  // ---------------- DOM ----------------
  if (typeof document === "undefined") return;
  var panel = document.getElementById("idem-lab");
  if (!panel) return;
  var state = createState();
  var rawBody = document.getElementById("idem-raw-body"), finBody = document.getElementById("idem-final-body");
  var rawCnt = document.getElementById("idem-raw-count"), finCnt = document.getElementById("idem-final-count");
  var verdict = document.getElementById("idem-verdict"), why = document.getElementById("idem-why");
  var history = document.getElementById("idem-history");

  function rowsHtml(rows, withToken) {
    if (!rows.length) return '<tr><td colspan="' + (withToken ? 3 : 2) + '">No rows</td></tr>';
    return rows.map(function (r) {
      return "<tr><td><code>" + r.key + "</code></td><td>" + r.body + "</td>" + (withToken ? "<td><code>" + r.token + "</code></td>" : "") + "</tr>";
    }).join("");
  }
  function render(accepted, message, label) {
    rawBody.innerHTML = rowsHtml(state.rows, true);
    var fin = finalView(state);
    finBody.innerHTML = rowsHtml(fin, false);
    rawCnt.textContent = String(rawCount(state));
    finCnt.textContent = String(fin.length);
    if (accepted !== null) {
      verdict.textContent = accepted ? "Accepted" : "Dropped as duplicate";
      verdict.className = "pill " + (accepted ? "pill--ok" : "pill--bad");
    } else { verdict.textContent = "No insert"; verdict.className = "pill"; }
    why.textContent = message;
    if (label) {
      var tr = document.createElement("tr");
      tr.innerHTML = "<td>" + label + "</td><td>" + (accepted === null ? "-" : accepted ? "accepted" : "dropped") + "</td><td>" + rawCount(state) + "</td><td>" + fin.length + "</td>";
      history.appendChild(tr);
    }
  }
  function doInsert(token, label) { var r = insert(state, token, CHUNK); state = r.state; render(r.accepted, r.message, label); }
  document.getElementById("idem-t1").addEventListener("click", function () { doInsert("T1", "Insert chunk (token T1)"); });
  document.getElementById("idem-replay").addEventListener("click", function () {
    if (state.tokens.indexOf("T1") === -1) { render(null, "Nothing to replay yet: insert the chunk with token T1 first.", ""); return; }
    doInsert("T1", "Replay same chunk (token T1)");
  });
  document.getElementById("idem-t2").addEventListener("click", function () { doInsert("T2", "Same rows, new token T2"); });
  document.getElementById("idem-merge").addEventListener("click", function () {
    var r = merge(state); state = r.state;
    render(null, r.removed ? "Merge removed " + r.removed + " rows that shared a sort key with another row. Stored rows now equal the FINAL view."
      : "Merge found nothing to collapse: every stored row already has its own sort key.", "Run merge");
  });
  document.getElementById("idem-reset").addEventListener("click", function () {
    state = createState(); history.innerHTML = ""; render(null, "Reset. The table is empty.", "");
  });
  render(null, "Empty table. Insert the chunk to start.", "");
})(typeof window !== "undefined" ? window : this);
