// Shop outreach (Admin): Open in your mail app copies the finished email first, then opens the mail app with only the
// address and subject. Some mail apps (Zoho Mail on Android) run every line of a message they are given together, so
// the owner pastes it instead, which keeps each line break. Without JavaScript, or when the browser will not copy,
// the link is the full mailto: link it always was. Nothing here talks to the server.
(function () {
  "use strict";
  // A button that sends a real email asks first, so a stray tap does not email a shop.
  document.addEventListener("click", function (event) {
    const sender = event.target.closest("button[data-confirm]");
    if (sender && !window.confirm(sender.dataset.confirm)) event.preventDefault();
  });
  document.addEventListener("click", function (event) {
    const link = event.target.closest("a[data-copy-from]");
    if (!link || !(navigator.clipboard && window.isSecureContext)) return;
    const box = document.getElementById(link.dataset.copyFrom);
    if (!box) return;
    event.preventDefault();
    navigator.clipboard.writeText(box.value).then(
      function () { window.location.href = link.dataset.short; },
      function () { window.location.href = link.href; }
    );
  });
})();
