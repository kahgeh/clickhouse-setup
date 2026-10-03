/* Module 3: filter the verification table by check family. */
(function () {
  var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-filter]"));
  var rows = Array.prototype.slice.call(document.querySelectorAll("#verify-table tbody tr"));
  var count = document.getElementById("verify-count");
  function apply(family) {
    var shown = 0;
    rows.forEach(function (tr) {
      var show = family === "all" || tr.getAttribute("data-family") === family;
      tr.hidden = !show; if (show) shown++;
    });
    buttons.forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-filter") === family ? "true" : "false"); });
    if (count) count.textContent = shown + " of " + rows.length + " checks shown";
  }
  buttons.forEach(function (b) { b.addEventListener("click", function () { apply(b.getAttribute("data-filter")); }); });
  if (buttons.length) apply("all");
})();
