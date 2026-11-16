// eBay health (Admin): Copy the log puts the whole Log to copy box on the clipboard, ready to paste into a message.
// Without JavaScript the button stays hidden and the box, which is plain text, is selected by hand. When the browser
// will not copy, the box is selected and the button says so. Nothing here talks to the server.
(function () {
  "use strict";
  document.querySelectorAll("button[data-copy-from]").forEach(function (button) { button.hidden = false; });
  document.addEventListener("click", function (event) {
    const button = event.target.closest("button[data-copy-from]");
    const box = button && document.getElementById(button.dataset.copyFrom);
    if (!box) return;
    const say = function (text) {
      button.textContent = text;
      window.setTimeout(function () { button.textContent = button.dataset.label; }, 2500);
    };
    const byHand = function () {
      box.focus();
      box.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch (error) { copied = false; }
      say(copied ? button.dataset.done : button.dataset.failed);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(box.value).then(function () { say(button.dataset.done); }, byHand);
    } else {
      byHand();
    }
  });
})();
