/* Renders pre.mermaid blocks in the page's theme. Load mermaid.min.js first.
   Colours and font are read from the page's CSS custom properties, so a Mermaid
   diagram follows theme.css in light and dark. Reload after changing theme.
   Diagrams keep their natural size (text stays legible) and scroll sideways in
   their frame when the page is narrower; message labels wrap at 200 units. */
(function () {
  function start() {
  if (typeof mermaid === "undefined") return;

  var canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  var ctx = canvas.getContext("2d", { willReadFrequently: true });

  /* Any CSS colour, including oklch(), to #rrggbb, which Mermaid can parse. */
  function hex(value) {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000000";
    ctx.fillStyle = value;
    ctx.fillRect(0, 0, 1, 1);
    var d = ctx.getImageData(0, 0, 1, 1).data;
    return "#" + [d[0], d[1], d[2]].map(function (n) { return ("0" + n.toString(16)).slice(-2); }).join("");
  }

  var root = getComputedStyle(document.documentElement);
  function token(name) { return hex(root.getPropertyValue(name).trim()); }

  var surface1 = token("--surface-1"), surface2 = token("--surface-2"), surface3 = token("--surface-3");
  var text1 = token("--text-1"), text2 = token("--text-2");
  var border = token("--border-strong"), accent = token("--accent");
  var noteBg = token("--warn-soft"), noteBorder = token("--warn"), noteText = token("--warn-text");

  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: "base",
    fontFamily: getComputedStyle(document.body).fontFamily,
    sequence: { useMaxWidth: false, mirrorActors: false, wrap: true, width: 200 },
    themeVariables: {
      background: surface1, textColor: text1, lineColor: text2,
      primaryColor: surface3, primaryTextColor: text1, primaryBorderColor: border,
      secondaryColor: surface2, tertiaryColor: surface1,
      actorBkg: surface3, actorBorder: border, actorTextColor: text1, actorLineColor: border,
      signalColor: text2, signalTextColor: text1,
      labelBoxBkgColor: surface2, labelBoxBorderColor: border, labelTextColor: text1, loopTextColor: text1,
      noteBkgColor: noteBg, noteBorderColor: noteBorder, noteTextColor: noteText,
      activationBkgColor: accent, activationBorderColor: accent, sequenceNumberColor: surface1
    }
  });
  mermaid.run({ querySelector: "pre.mermaid" }).then(function () {
    Array.prototype.forEach.call(document.querySelectorAll("pre.mermaid"), function (el) { el.tabIndex = 0; });
  });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
