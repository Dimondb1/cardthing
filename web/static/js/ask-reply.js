// The Ask popup asks Rip in place (the owner, 15 Nov 2026, from the suggestions: his reply to small talk in the popup
// itself, not a new page). What was typed, or what the microphone heard (ripask, from ask-input.js), goes to
// web.views.voice_search as the popup's own request (X-Rip-Sheet): the same words the form sends. His reply to small
// talk comes back and ask-input.js shows it (ripreply); anything else opens where it would have. Kept apart from
// ask-input.js, which never sends anything itself. Without fetch, or if anything goes wrong on the way, the page goes
// as the form would.
(function () {
  "use strict";

  var form = document.querySelector("[data-ask-sheet-form]");
  var sheet = form && form.closest("[data-ask-sheet]");
  if (!sheet || !sheet.querySelector("[data-ask-reply]")) return;
  if (!window.fetch || !window.URLSearchParams || !window.FormData || !window.CustomEvent) return;

  var answer = function (query) {
    var address = form.getAttribute("action") + "?" + query.toString();
    fetch(address, { headers: { "X-Rip-Sheet": "1", "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("not answered");
        return response.json();
      })
      .then(function (data) {
        var asked = (query.get("q") || "").trim();
        if (data.chat) form.dispatchEvent(new CustomEvent("ripreply", { detail: { chat: data.chat, q: asked } }));
        else {
          // Noted for the next page's Ask Rip, beside the page (ask-input.js, ripNote).
          if (asked && window.ripNote) window.ripNote({ q: asked, go: data.go });
          window.location.href = data.go;
        }
      })
      .catch(function () { window.location.href = address; });
  };

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    answer(new URLSearchParams(new FormData(form)));
  });
  form.addEventListener("ripask", function (event) {
    event.preventDefault();
    answer(event.detail.query);
  });
})();
