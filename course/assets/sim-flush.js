/* Module 11: flush-interval cost sensitivity. Reproduces the S3 request part of cost/model.py (base scenario).
   Constants are the ones the model states; every one is an assumption or a price, labelled on the page.
     PUTs per month = max(2,592,000 / flush_s, buffer bytes per collector per signal / flush_size_bytes)
                      x collectors x signals x 1.25
     GETs per month = objects written x 1 + manifest polls (2,592,000 / poll_s x signals) */
(function (root) {
  "use strict";
  var K = {
    monthSeconds: 30 * 86400,
    collectors: 3, signals: 2, putsPerFlush: 1.25, getsPerObject: 1.0, pollSeconds: 1.0,
    flushSizeBytes: 4 * 1048576,
    // buffer bytes per month per signal (zstd-compressed OTLP), from the model's assumptions
    bufferBytes: [2.78e9 * 650 / 5, 20e9 * 80 / 4],
    putUsd: 0.0055 / 1000, getUsd: 0.0044 / 10000
  };
  var STEPS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120];

  function s3Requests(flushSeconds) {
    var objects = 0;
    K.bufferBytes.forEach(function (b) {
      var timeDriven = K.monthSeconds / flushSeconds;
      var sizeDriven = (b / K.collectors) / K.flushSizeBytes;
      objects += Math.max(timeDriven, sizeDriven) * K.collectors;
    });
    var puts = objects * K.putsPerFlush;
    var gets = objects * K.getsPerObject + (K.monthSeconds / K.pollSeconds) * K.signals;
    return { flushSeconds: flushSeconds, puts: puts, gets: gets, usd: puts * K.putUsd + gets * K.getUsd,
             putUsd: puts * K.putUsd, latencySeconds: flushSeconds };
  }

  var api = { K: K, STEPS: STEPS, s3Requests: s3Requests };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof document === "undefined") return;
  var slider = document.getElementById("fl-slider");
  if (!slider) return;

  function fmtM(n) { return (n / 1e6).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " M"; }
  function fmtUsd(n) { return "$" + Math.round(n).toLocaleString("en-US"); }
  function fmtS(s) { return (s < 1 ? String(s) : String(s)) + " s"; }
  var out = document.getElementById("fl-out"), val = document.getElementById("fl-val"), tbody = document.getElementById("fl-tbody");
  tbody.textContent = "";
  var rows = STEPS.map(function (s) {
    var r = s3Requests(s), tr = document.createElement("tr");
    tr.innerHTML = '<th scope="row">' + fmtS(s) + '</th><td class="num">' + fmtM(r.puts) + '</td><td class="num">' + fmtUsd(r.usd) + '</td><td class="num">about ' + fmtS(s) + "</td>";
    tbody.appendChild(tr);
    return tr;
  });
  slider.min = "0"; slider.max = String(STEPS.length - 1); slider.step = "1";
  function update() {
    var i = +slider.value, s = STEPS[i], r = s3Requests(s), base = s3Requests(5);
    val.textContent = fmtS(s);
    slider.setAttribute("aria-valuetext", fmtS(s) + " flush interval");
    out.innerHTML = "Flush every <strong>" + fmtS(s) + "</strong>: about <strong>" + fmtM(r.puts) + "</strong> PUT requests and <strong>" + fmtM(r.gets) +
      "</strong> GET requests a month, about <strong>" + fmtUsd(r.usd) + "</strong> of S3 requests, and about " + fmtS(s) +
      " added before data can be queried. The 5 s setting in the model's base scenario is " + fmtUsd(base.usd) + ".";
    rows.forEach(function (tr, k) { tr.className = k === i ? "changed" : ""; });
  }
  slider.value = String(STEPS.indexOf(5));
  slider.addEventListener("input", update);
  update();
})(typeof window !== "undefined" ? window : this);
