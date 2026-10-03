// Unit tests for the pure functions of assets/sim-alert.js. Run from course/: node tools/test-sim-alert.js
const A = require("../assets/sim-alert.js"), assert = require("assert");
const base = { failures: 4, threshold: 4, op: ">", second: 20, delay: 1 };
const t = (name, fn) => { fn(); console.log("ok  " + name); };
t("exactly 4 with > does not fire", () => { const r = A.evaluate(base); assert.strictEqual(r.counted, 4); assert.strictEqual(r.fires, false); assert.strictEqual(r.outcome, "no-fire-equal"); });
t("exactly 4 with >= fires", () => { const r = A.evaluate({ ...base, op: ">=" }); assert.strictEqual(r.fires, true); assert.strictEqual(r.outcome, "fires"); });
t("5 with > fires", () => assert.strictEqual(A.evaluate({ ...base, failures: 5 }).fires, true));
t("3 with >= does not fire", () => assert.strictEqual(A.evaluate({ ...base, failures: 3, op: ">=" }).fires, false));
t("zero failures never fires, either comparison, thresholds 1..100", () => {
  for (const op of [">", ">="]) for (let th = 1; th <= 100; th++) for (const d of [0, 15, 30]) assert.strictEqual(A.evaluate({ failures: 0, threshold: th, op, second: 59, delay: d }).fires, false);
});
t("rows arriving after :00 are missed by the evaluation of their minute", () => {
  const r = A.evaluate({ failures: 60, threshold: 4, op: ">", second: 55, delay: 10 });
  assert.strictEqual(r.window, 0); assert.strictEqual(r.late, true); assert.strictEqual(r.counted, 0); assert.strictEqual(r.fires, false); assert.strictEqual(r.outcome, "missed"); assert.strictEqual(r.wouldFireIfCounted, true);
});
t("arrival exactly at :00 is treated as late", () => assert.strictEqual(A.evaluate({ failures: 10, threshold: 4, op: ">", second: 50, delay: 10 }).late, true));
t("arrival one second before :00 is counted", () => assert.strictEqual(A.evaluate({ failures: 10, threshold: 4, op: ">", second: 50, delay: 9 }).counted, 10));
t("failures in the next minute are counted in the next window, which fires", () => {
  const r = A.evaluate({ failures: 10, threshold: 4, op: ">", second: 65, delay: 2 });
  assert.strictEqual(r.window, 1); assert.strictEqual(r.evalAt, 120); assert.strictEqual(r.counted, 10); assert.strictEqual(r.outcome, "fires-next");
});
t("describe never throws", () => { for (const o of [{ ...base }, { ...base, failures: 0 }, { ...base, second: 59, delay: 30, failures: 9 }, { ...base, second: 70, failures: 9 }, { ...base, failures: 2 }]) assert.ok(A.describe(o, A.evaluate(o)).length > 10); });
