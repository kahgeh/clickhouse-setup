const assert = require("assert");
const F = require("../assets/sim-dataflow.js");
const run = (s, secs) => F.step(s, secs);
const ok = (s) => {
  const a = F.accounting(s);
  assert.strictEqual(a.unaccounted, 0, "no unaccounted rows");
  assert.strictEqual(a.written, a.emitted - a.lost - a.inCollector, "written = produced - lost - still in the collector");
};

// baseline
let s = run(F.create(), 20); ok(s);
let d = F.drain(s); assert.strictEqual(F.accounting(d).lost, 0); assert.strictEqual(d.clickhouse, d.emitted);

// S3 outage of 30 s (shorter than the retry window): nothing lost
s = run(F.create(), 10); s = F.setUp(s, "s3", false); s = run(s, 30); ok(s);
assert(F.accounting(s).inCollector > 0, "batches wait in collector memory during the outage");
s = F.setUp(s, "s3", true); d = F.drain(s);
assert.strictEqual(F.accounting(d).lost, 0); assert.strictEqual(d.clickhouse, d.emitted); ok(d);
console.log("S3 outage 30 s: lost 0, ClickHouse = produced =", d.emitted);

// ingestor down 60 s: queue builds, nothing lost
s = run(F.create(), 5); s = F.setUp(s, "ingestor", false); s = run(s, 60);
assert(F.accounting(s).queueDepth > 0); ok(s);
s = F.setUp(s, "ingestor", true); d = F.drain(s);
assert.strictEqual(F.accounting(d).lost, 0); assert.strictEqual(d.clickhouse, d.emitted);
console.log("ingestor down 60 s: peak queue", F.accounting(run(F.setUp(run(F.create(), 5), "ingestor", false), 60)).queueDepth, "rows, lost 0, ClickHouse = produced =", d.emitted);

// ClickHouse down 20 s
s = run(F.create(), 5); s = F.setUp(s, "clickhouse", false); s = run(s, 20);
assert(F.accounting(s).queueDepth > 0); ok(s);
s = F.setUp(s, "clickhouse", true); d = F.drain(s);
assert.strictEqual(F.accounting(d).lost, 0); assert.strictEqual(d.clickhouse, d.emitted);
console.log("ClickHouse down 20 s: lost 0, ClickHouse = produced =", d.emitted);

// kill between insert and acknowledge: replay dropped, no duplicates, no loss
s = run(F.create(), 5); s = F.killMidCommit(s); s = run(s, 3);
assert.strictEqual(s.up.ingestor, false, "ingestor died");
assert.strictEqual(F.accounting(s).dropped, 0);
s = F.setUp(s, "ingestor", true); d = F.drain(s); ok(d);
assert(F.accounting(d).dropped > 0, "replay dropped by token");
assert.strictEqual(d.clickhouse, d.emitted, "no duplicate rows, no loss");
assert.strictEqual(F.accounting(d).lost, 0);
console.log("kill mid-commit: dropped as duplicates =", F.accounting(d).dropped, "rows, ClickHouse = produced =", d.emitted);

// collector crash loses the in-memory batch (the one loss case)
s = run(F.create(), 10); const mem = F.accounting(s).inCollector; assert(mem > 0);
s = F.crashCollector(s); assert.strictEqual(F.accounting(s).lost, mem); ok(s);
d = F.drain(s); assert.strictEqual(d.clickhouse, d.emitted - mem); assert.strictEqual(F.accounting(d).lost, mem); ok(d);
console.log("collector crash: lost", mem, "rows (the in-memory batch); everything else delivered");

// beyond the retry window the model drops data (extrapolation, flagged on the page)
s = F.setUp(run(F.create(), 1), "s3", false); s = run(s, F.RETRY_WINDOW + 5); assert(F.accounting(s).lost > 0); ok(s);
console.log("S3 outage longer than retry window (modelled, not tested): lost", F.accounting(s).lost);

// healthy run: the queue is empty at the end of every second, but the running total shows rows did reach the Buffer
(function () {
  var s = F.create();
  s = F.step(s, 1); assert.strictEqual(F.accounting(s).written, 0);        // the first rows are still in the collector
  s = F.step(s, 1); assert.strictEqual(F.accounting(s).queueDepth, 0);     // drained within the same second
  assert.strictEqual(F.accounting(s).written, F.EMIT);                     // ...but they were written
  console.log("healthy: queue depth 0 yet written =", F.accounting(s).written);
})();
console.log("dataflow: all assertions passed");
