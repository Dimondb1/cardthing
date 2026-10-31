// Inlined right after the home page's drop spotlight (web/templates/web/includes/spotlight.html), so it runs before
// the panel is painted: a panel this browser hid stays a slim bar, and so does one whose alerts were set here, until
// it is shown again (spotlight.js keeps the choice). Nothing is sent anywhere; without storage the panel stays open.
// (Inlined, so it is written without exclamation marks: the house style tests read it.)
(function () {
  var panel = document.currentScript ? document.currentScript.previousElementSibling : null;
  if (panel === null || panel.hasAttribute("data-spotlight-panel") === false) return;
  try {
    var set = panel.getAttribute("data-set");
    var said = localStorage.getItem("rr-spotlight:" + set);
    var done = JSON.parse(localStorage.getItem("rr-prep:" + set) || "[]");
    var alerted = done.indexOf("alerts") > -1;
    if (alerted) panel.querySelector("[data-spotlight-alerted]").hidden = false;
    if (said === "closed" || (said === null && alerted)) panel.open = false;
  } catch (e) {
    // No storage: the panel opens as the page sent it.
  }
})();
