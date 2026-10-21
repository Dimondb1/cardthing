// Share: the round logos in a product page's Share panel, and on pages of product cards, where one dialog serves
// every card. Instagram, TikTok and Copy link put the product's link, marked with where it was shared, on the
// clipboard and say where to paste it; More opens the phone's share sheet. Without a clipboard the address
// stays in its box to copy by hand. Without this file a card's share icon is a link to its product page's
// Share panel, which works without script.
(() => {
  const tagged = (page, source) => {
    const url = new URL(page);
    url.hash = "";
    url.search = new URLSearchParams({ utm_source: source, utm_medium: "social", utm_campaign: "product_share" }).toString();
    return url.href;
  };

  // Each button reads its link when tapped, so the dialog can change them for every card.
  document.querySelectorAll("[data-share]").forEach((box) => {
    const link = box.querySelector("[data-share-link]");
    const fallback = box.querySelector("[data-share-fallback]");
    const status = box.querySelector("[data-share-status]");
    link.addEventListener("focus", () => link.select());
    if (navigator.clipboard && window.isSecureContext) {
      fallback.hidden = true;
      box.querySelectorAll("[data-share-copy]").forEach((item) => {
        item.hidden = false;
        item.querySelector("button").addEventListener("click", () => {
          navigator.clipboard.writeText(item.dataset.shareCopy).then(
            () => { status.textContent = item.dataset.shareDone; },
            () => { link.value = item.dataset.shareCopy; fallback.hidden = false; link.focus(); },
          );
        });
      });
    }
    const native = box.querySelector("[data-share-native]");
    if (native && navigator.share) {
      native.hidden = false;
      native.querySelector("button").addEventListener("click", () => {
        navigator.share({ title: box.dataset.shareTitle, url: native.dataset.shareUrl }).catch(() => {});
      });
    }
  });

  // A card's share icon fills the dialog with its product's links and opens it. Cards a tab fetches later are
  // caught too, as the click is heard on the page.
  const dialog = document.querySelector("[data-share-dialog]");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const box = dialog.querySelector("[data-share]");
  document.addEventListener("click", (event) => {
    const open = event.target.closest("[data-card-share]");
    if (!open) return;
    event.preventDefault();
    const page = new URL(open.getAttribute("href"), location.href);
    page.hash = "";
    const name = open.dataset.cardShare;
    box.dataset.shareTitle = name;
    dialog.querySelector("[data-share-name]").textContent = name;
    dialog.querySelector("[data-share-reddit]").href = "https://www.reddit.com/submit?" + new URLSearchParams({
      url: tagged(page, "reddit"), title: dialog.dataset.redditTitle.replace("{product}", name),
    }).toString();
    box.querySelectorAll("[data-share-network]").forEach((item) => { item.dataset.shareCopy = tagged(page, item.dataset.shareNetwork); });
    const native = box.querySelector("[data-share-native]");
    if (native) native.dataset.shareUrl = tagged(page, "share_sheet");
    box.querySelector("[data-share-link]").value = page.href;
    box.querySelector("[data-share-status]").textContent = "";
    dialog.showModal();
  });
  // Close, or a tap on the shaded page around the sheet, closes it; so does Escape, as for any dialog.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-share-close]")) dialog.close();
  });
})();
