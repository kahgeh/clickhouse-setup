/* Course-wide behaviour: remember which modules were opened and show it on the landing page.
   Progress is a convenience only. If storage is blocked the course works the same, just without ticks. */
(function () {
  var KEY = "obs-course-visited";
  function read() {
    try { return JSON.parse(window.localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
  }
  function write(list) {
    try { window.localStorage.setItem(KEY, JSON.stringify(list)); } catch (e) { /* storage blocked: ignore */ }
  }
  function start() {
    var visited = read();
    var here = document.body.getAttribute("data-module");
    if (here && visited.indexOf(here) === -1) { visited.push(here); write(visited); }
    var items = document.querySelectorAll("ul.mods li[data-module]");
    var done = 0;
    Array.prototype.forEach.call(items, function (li) {
      if (visited.indexOf(li.getAttribute("data-module")) !== -1) { li.classList.add("is-done"); done++; }
    });
    var bar = document.querySelector("progress.progress");
    if (bar && items.length) { bar.max = items.length; bar.value = done; }
    var label = document.querySelector("[data-progress-label]");
    if (label && items.length) label.textContent = done + " of " + items.length + " modules opened";
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
