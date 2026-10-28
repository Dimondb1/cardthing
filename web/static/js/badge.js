// The badge part of List your shop: Copy code for each badge. Without JavaScript the code sits in a box to select.
// Nothing here talks to the server.
(function () {
  "use strict";
  document.querySelectorAll("[data-badge]").forEach(function (item) {
    const box = item.querySelector("[data-badge-code]");
    const copy = item.querySelector("[data-badge-copy]");
    const copied = item.querySelector("[data-badge-copied]");
    box.addEventListener("focus", function () { box.select(); });
    if (!(navigator.clipboard && window.isSecureContext)) return;
    copy.hidden = false;
    copy.querySelector("button").addEventListener("click", function () {
      navigator.clipboard.writeText(box.value).then(function () { copied.hidden = false; }, function () { box.focus(); });
    });
  });
})();
