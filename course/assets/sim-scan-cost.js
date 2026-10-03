/* Module 1: per-scan price against provisioned capacity. Assumptions are stated on the page. */
(function () {
  var tb = document.getElementById("sc-tb"), pr = document.getElementById("sc-prune"), sp = document.getElementById("sc-speed");
  var tbo = document.getElementById("sc-tb-o"), pro = document.getElementById("sc-prune-o"), out = document.getElementById("sc-out");
  var SECONDS = 30 * 86400, PRICE_GB = 0.0067, VCPU_HOUR = 0.0604, PEAK = 5, HOURS = 720;
  function fmt(n) { return Math.round(n).toLocaleString("en-US"); }
  function update() {
    var teras = +tb.value, prune = +pr.value / 100, rate = +sp.value;
    var scanBill = teras * 1000 * PRICE_GB;
    var avg = teras * 1000 * (1 - prune) / SECONDS;
    var vcpu = avg / rate * PEAK;
    var compute = vcpu * VCPU_HOUR * HOURS;
    tbo.textContent = fmt(teras); pro.textContent = Math.round(prune * 100) + "%";
    out.innerHTML = "Per-scan price: <strong>$" + fmt(scanBill) + "</strong> a month. Provisioned capacity for the same searches: about <strong>" +
      Math.max(1, Math.ceil(vcpu)) + " vCPU at peak</strong>, roughly <strong>$" + fmt(compute) + "</strong> a month. Doubling the scanned volume doubles the first number; the second grows only if the load needs more vCPU.";
  }
  [tb, pr, sp].forEach(function (el) { el.addEventListener("input", update); });
  update();
})();
