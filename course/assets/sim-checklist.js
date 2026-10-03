/* Module 8: setup checklist with remembered progress, and a "where does my data live?" lookup.
   Lookup logic is pure and testable with node; storage access is guarded because it can throw. */
(function (root) {
  "use strict";
  var KEY = "course-m08-setup-checklist";

  var SIGNALS = {
    logs: { label: "Logs", pipeline: "logs", manifest: "otel/logs/manifest", data: "otel/logs/data", ingestor: "ingestor-logs", port: 9091,
            tables: ["otel.otel_logs"], source: "Logs",
            query: "SELECT SeverityText, count() FROM otel.otel_logs GROUP BY SeverityText" },
    traces: { label: "Traces", pipeline: "traces", manifest: "otel/traces/manifest", data: "otel/traces/data", ingestor: "ingestor-traces", port: 9092,
            tables: ["otel.otel_traces"], source: "Traces",
            query: "SELECT SpanName, count() FROM otel.otel_traces GROUP BY SpanName" },
    metrics: { label: "Metrics", pipeline: "metrics", manifest: "otel/metrics/manifest", data: "otel/metrics/data", ingestor: "ingestor-metrics", port: 9093,
            tables: ["otel.otel_metrics_gauge", "otel.otel_metrics_sum", "otel.otel_metrics_histogram"], source: "Metrics",
            query: "SELECT MetricName, count() FROM otel.otel_metrics_sum GROUP BY MetricName" }
  };
  var STAGES = ["collector", "buffer", "ingestor", "clickhouse", "hyperdx"];
  var STAGE_LABEL = { collector: "In the collector", buffer: "In the Buffer on S3", ingestor: "In the ingestor",
                      clickhouse: "In ClickHouse", hyperdx: "In HyperDX" };

  function lookup(signal, stage) {
    var s = SIGNALS[signal];
    if (!s || STAGES.indexOf(stage) === -1) return null;
    switch (stage) {
      case "collector": return { where: "Pipeline " + s.pipeline + " in the collector, exporter opendata/" + s.pipeline,
        detail: "The data is a batch in the collector's memory until the write to the Buffer succeeds. A collector crash loses what is still in memory.", query: "" };
      case "buffer": return { where: "Manifest " + s.manifest + " and data objects under " + s.data + " in the bucket observability-buffer",
        detail: "The manifest lists the batches that are waiting. Consumed batches are removed later.", query: "" };
      case "ingestor": return { where: "Container " + s.ingestor + ", metrics on port " + s.port,
        detail: "The ingestor reads the manifest, inserts chunks with a token and acknowledges the range afterwards.", query: "curl -s localhost:" + s.port + "/metrics" };
      case "clickhouse": return { where: "Table" + (s.tables.length > 1 ? "s " : " ") + s.tables.join(", ") + " in the otel database",
        detail: s.tables.length > 1 ? "Gauges, sums (counters) and histograms are stored in separate tables." : "Rows carry the provenance columns _odb_sequence, _odb_entry_index and _odb_record_index.",
        query: s.query };
      default: return { where: "The " + s.source + " source in HyperDX (http://localhost:8080)",
        detail: "HyperDX reads the ClickHouse tables above with the read-only user hyperdx_ro. It is not in the data path.", query: "" };
    }
  }

  function progress(checked, total) { return checked + " of " + total + " steps done"; }

  var api = { SIGNALS: SIGNALS, STAGES: STAGES, lookup: lookup, progress: progress, KEY: KEY };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof document === "undefined") return;

  // ---------- checklist ----------
  var boxes = Array.prototype.slice.call(document.querySelectorAll("input[data-step]"));
  var out = document.getElementById("chk-progress");
  function read() { try { return JSON.parse(root.localStorage.getItem(KEY) || "{}") || {}; } catch (e) { return {}; } }
  function write(o) { try { root.localStorage.setItem(KEY, JSON.stringify(o)); } catch (e) { /* storage unavailable: progress is kept for this visit only */ } }
  function update() {
    var n = boxes.filter(function (b) { return b.checked; }).length;
    if (out) out.textContent = progress(n, boxes.length);
  }
  if (boxes.length) {
    var saved = read();
    boxes.forEach(function (b) {
      b.checked = !!saved[b.getAttribute("data-step")];
      b.addEventListener("change", function () {
        var o = read(); o[b.getAttribute("data-step")] = b.checked; write(o); update();
      });
    });
    var reset = document.getElementById("chk-reset");
    if (reset) reset.addEventListener("click", function () { boxes.forEach(function (b) { b.checked = false; }); write({}); update(); });
    update();
  }

  // ---------- lookup ----------
  var selSig = document.getElementById("loc-signal"), selStage = document.getElementById("loc-stage");
  var where = document.getElementById("loc-where"), detail = document.getElementById("loc-detail"), q = document.getElementById("loc-query");
  function show() {
    var r = lookup(selSig.value, selStage.value);
    if (!r) return;
    where.textContent = r.where; detail.textContent = r.detail;
    q.textContent = r.query ? "Try: " + r.query : "";
  }
  if (selSig && selStage) { selSig.addEventListener("change", show); selStage.addEventListener("change", show); show(); }
})(typeof window !== "undefined" ? window : this);
