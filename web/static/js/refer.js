// The refer a friend share page: Copy link and the phone's own share sheet. Without JavaScript the link sits in
// a box to select, and WhatsApp, text and email are plain links. Nothing here talks to the server.
(function () {
  "use strict";
  const box = document.querySelector("[data-refer-share]");
  if (!box) return;
  const link = box.querySelector("[data-refer-link]");
  const copied = box.querySelector("[data-refer-copied]");
  const copyItem = box.querySelector("[data-refer-copy]");
  const nativeItem = box.querySelector("[data-refer-native]");

  link.addEventListener("focus", function () { link.select(); });

  if (navigator.clipboard && window.isSecureContext) {
    copyItem.hidden = false;
    copyItem.querySelector("button").addEventListener("click", function () {
      navigator.clipboard.writeText(link.value).then(function () { copied.hidden = false; }, function () {
        link.focus();
      });
    });
  }

  if (navigator.share) {
    nativeItem.hidden = false;
    nativeItem.querySelector("button").addEventListener("click", function () {
      navigator.share({ title: box.dataset.shareTitle, text: box.dataset.shareText }).catch(function () {});
    });
  }
})();
