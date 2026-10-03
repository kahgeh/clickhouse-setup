/* Module 2: tabs that show the real payload at each hop. Panels are ordinary <pre> elements in the page. */
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-payload-tab]"));
  if (!tabs.length) return;
  var panels = Array.prototype.slice.call(document.querySelectorAll("[data-payload-panel]"));
  function show(key) {
    tabs.forEach(function (t) { t.setAttribute("aria-pressed", t.getAttribute("data-payload-tab") === key ? "true" : "false"); });
    panels.forEach(function (p) { p.hidden = p.getAttribute("data-payload-panel") !== key; });
  }
  tabs.forEach(function (t) { t.addEventListener("click", function () { show(t.getAttribute("data-payload-tab")); }); });
  show(tabs[0].getAttribute("data-payload-tab"));
})();
