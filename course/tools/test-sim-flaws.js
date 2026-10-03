const assert = require("assert");
const F = require("../assets/sim-flaws.js");
assert.strictEqual(F.SCENARIOS.length, 7);
const ids = new Set();
for (const s of F.SCENARIOS) {
  assert(!ids.has(s.id)); ids.add(s.id);
  assert(s.checks.length >= 3 && s.checks.length <= 4, s.id);
  assert(s.checks.some((c) => c.v === "yes"), s.id + " has a catching check");
  assert(s.checks.some((c) => c.v === "no"), s.id + " has a non-catching check");
  s.checks.forEach((c, i) => { const r = F.evaluate(s.id, i); assert(r && r.why && r.label && r.all.length === s.checks.length); assert(["yes","partly","no"].includes(c.v)); });
}
assert.strictEqual(F.evaluate("boundary", 0).verdict, "yes");
assert.strictEqual(F.evaluate("boundary", 1).verdict, "no");
assert.strictEqual(F.evaluate("nope", 0), null);
console.log("flaws: 7 scenarios, every one has a catching and a non-catching check; evaluate() returns verdict, reason and all verdicts");
