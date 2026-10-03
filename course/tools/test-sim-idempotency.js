const assert = require("assert");
const L = require("../assets/sim-idempotency.js");
let s = L.createState();
let r = L.insert(s, "T1", L.CHUNK); s = r.state;
assert(r.accepted && L.rawCount(s) === 2, "first insert stores 2 rows");
for (let i = 0; i < 5; i++) { r = L.insert(s, "T1", L.CHUNK); assert(!r.accepted && r.added === 0); s = r.state; assert.strictEqual(L.rawCount(s), 2, "replay with same token never changes count"); }
r = L.insert(s, "T2", L.CHUNK); s = r.state;
assert(r.accepted && L.rawCount(s) === 4, "new token adds rows (raw 4)");
assert.strictEqual(L.finalView(s).length, 2, "FINAL collapses to 2");
assert.strictEqual(L.finalView(s).length, L.distinctKeys(s));
const m = L.merge(s); assert.strictEqual(L.rawCount(m.state), 2); assert.strictEqual(m.removed, 2);
// distinct keys property with several tokens
let t = L.createState(); for (let i = 0; i < 7; i++) t = L.insert(t, "X" + i, L.CHUNK).state;
assert.strictEqual(L.rawCount(t), 14); assert.strictEqual(L.finalView(t).length, 2);
console.log("idempotency: all assertions passed (replay raw stays 2; T2 raw 4; FINAL 2; merge raw 2; 7 tokens raw 14 FINAL 2)");
