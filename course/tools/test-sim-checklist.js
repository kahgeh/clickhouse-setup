const assert = require("assert");
const C = require("../assets/sim-checklist.js");
let n = 0;
for (const sig of Object.keys(C.SIGNALS)) for (const st of C.STAGES) { const r = C.lookup(sig, st); assert(r && r.where && r.detail, sig + "/" + st); n++; }
assert.strictEqual(C.lookup("logs", "buffer").where.includes("otel/logs/manifest"), true);
assert.strictEqual(C.lookup("metrics", "clickhouse").where.includes("otel_metrics_histogram"), true);
assert.strictEqual(C.lookup("traces", "clickhouse").query.includes("otel.otel_traces"), true);
assert.strictEqual(C.lookup("nope", "buffer"), null);
assert.strictEqual(C.progress(3, 9), "3 of 9 steps done");
console.log("checklist: " + n + " signal/stage lookups return a location; unknown input returns null");
