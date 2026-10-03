/* Module 2: click or press Enter on a stage to see what it does and how it fails. Text only; no external data. */
(function () {
  var STAGES = {
    apps: {
      name: "Applications (web and backend)",
      does: "Emit logs, metrics and traces over OTLP. A request carries a trace identifier from the web service to the backend over gRPC, so spans and log lines from both services can be joined later.",
      fails: "If the collector is unreachable, the OpenTelemetry SDK retries and then drops from its own queue. A retry after a lost acknowledgement can send the same data twice, so this first hop is at-least-once.",
      evid: "See module 4 for how the trace identifier travels."
    },
    collector: {
      name: "OpenTelemetry collector",
      does: "Receives OTLP, batches it, and writes each batch to the Buffer. It runs one pipeline per signal, each with its own queue. The exporter is the OpenData exporter with two local patches: a traces signal and retry on failure.",
      fails: "A write to object storage is retried with backoff for up to five minutes by default. Batches held in the collector's memory when it crashes are lost, because they are not yet durable.",
      evid: "Module 10 shows what the unpatched exporter did to data during an outage."
    },
    buffer: {
      name: "OpenData Buffer on S3",
      does: "A durable queue made of objects in a bucket. A manifest file lists the batches, and it is updated with a compare-and-set write so several writers cannot overwrite each other. There is one manifest per signal. Consumed batches are removed later.",
      fails: "While S3 is unavailable the collector retries and nothing is lost within the retry window. Data already written keeps waiting until an ingestor reads it.",
      evid: "Module 9: an S3 outage of 30 seconds under load lost nothing."
    },
    ingestors: {
      name: "Ingestors (one per signal)",
      does: "Poll a manifest, fetch each batch, decode the OTLP, and insert rows into ClickHouse in chunks. Each chunk carries a deterministic idempotency token, and the ingestor acknowledges the range only after ClickHouse accepts it. An entry that is not valid protobuf is counted and skipped.",
      fails: "After a crash the same range is read again and inserted with the same token, so ClickHouse drops the repeat. If ClickHouse is down the ingestor retries and the data stays in the Buffer.",
      evid: "Module 9 shows the crash tests, including how many replays ClickHouse dropped."
    },
    clickhouse: {
      name: "ClickHouse",
      does: "Stores logs, spans and metric points in tables shaped like HyperDX's default schema, plus provenance columns that record where each row came from. The tables use ReplacingMergeTree with the provenance key in the sort key, so duplicates collapse on merge.",
      fails: "During an outage inserts fail and the ingestors retry. Reads from HyperDX fail too, but ingestion resumes without data loss once it is back.",
      evid: "Module 7 explains the deduplication."
    },
    hyperdx: {
      name: "HyperDX",
      does: "The interface for searching, dashboards and alerts. It reads ClickHouse with a read-only user, and its sources, dashboards, saved searches and alerts are created from code through its API.",
      fails: "If HyperDX is down, searching stops but ingestion is unaffected, because it is not in the data path.",
      evid: "Modules 5 and 6 cover dashboards and alerts."
    },
    webhook: {
      name: "Alert webhook",
      does: "HyperDX posts a notification here when an alert fires and again when it resolves. In this stack a small receiver records the notifications so tests can check them.",
      fails: "HyperDX evaluates alerts once a minute, so a failure that lands in ClickHouse after the evaluation moment is not counted until the next window, and rows arriving late may never be counted.",
      evid: "Module 6 shows the firing and resolve notifications."
    }
  };
  var panel = document.getElementById("arch-panel");
  if (!panel) return;
  var nodes = Array.prototype.slice.call(document.querySelectorAll("svg [data-node]"));
  function show(key) {
    var s = STAGES[key]; if (!s) return;
    nodes.forEach(function (g) {
      var on = g.getAttribute("data-node") === key;
      g.querySelector("rect").setAttribute("class", on ? "d-node d-node--busy" : "d-node");
      g.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.getElementById("arch-name").textContent = s.name;
    document.getElementById("arch-does").textContent = "What it does. " + s.does;
    document.getElementById("arch-fails").textContent = "How it fails. " + s.fails;
    document.getElementById("arch-evid").textContent = s.evid;
  }
  nodes.forEach(function (g) {
    g.addEventListener("click", function () { show(g.getAttribute("data-node")); });
    g.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(g.getAttribute("data-node")); }
    });
  });
  show("collector");
})();
