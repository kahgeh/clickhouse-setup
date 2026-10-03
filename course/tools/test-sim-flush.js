const assert = require("assert"), fs = require("fs"), path = require("path");
const F = require("../assets/sim-flush.js");
const txt = fs.readFileSync(path.join(__dirname, "../evidence/cost-model-output.txt"), "utf8");
const block = txt.split("Sensitivity:")[1].split("Buffer alternative")[0];
const re = /^\s+([\d.]+)s\s+([\d.,]+) M\s+\$([\d,]+)/gm;
let m, n = 0;
while ((m = re.exec(block))) {
  const flush = +m[1], putsM = +m[2], usd = +m[3].replace(/,/g, "");
  const r = F.s3Requests(flush);
  assert(Math.abs(r.puts / 1e6 - putsM) < 0.05 + 1e-9, `PUTs ${flush}: ${r.puts / 1e6} vs ${putsM}`);
  assert(Math.abs(r.usd - usd) <= 0.5 + 1e-9, `usd ${flush}: ${r.usd} vs ${usd}`);
  console.log(`flush ${flush} s: model file ${putsM} M PUTs $${usd}; function ${(r.puts / 1e6).toFixed(1)} M PUTs $${r.usd.toFixed(1)}`);
  n++;
}
assert.strictEqual(n, 6, "all six table rows compared");
const base = F.s3Requests(5), fast = F.s3Requests(0.1);
assert(Math.abs(base.puts - 3.888e6) < 1 && Math.round(base.usd) === 25);
assert(Math.abs(fast.puts - 194.4e6) < 1 && Math.round(fast.usd) === 1140);
// monotonic: longer flush never costs more
for (let i = 1; i < F.STEPS.length; i++) assert(F.s3Requests(F.STEPS[i]).usd <= F.s3Requests(F.STEPS[i - 1]).usd);
console.log("flush: all assertions passed; 5 s = " + (base.puts/1e6).toFixed(2) + " M PUTs $" + base.usd.toFixed(2) + ", 0.1 s = " + (fast.puts/1e6).toFixed(1) + " M $" + fast.usd.toFixed(2));
