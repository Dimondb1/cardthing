// Share: the round logos in a product page's Share panel, and on pages of product cards, where one dialog serves
// every card. Post makes an Instagram post's picture and caption, and Story an Instagram story's picture (below).
// Instagram, TikTok and Copy link put the product's link, marked with where it was shared, on the clipboard and say
// where to paste it; More opens the phone's share sheet. Without a clipboard the address
// stays in its box to copy by hand. Without this file a card's share icon is a link to its product page's
// Share panel, which works without script.
(() => {
  // A picture saved where the browser keeps downloads: the way out when it cannot be shared.
  const saveFile = (file) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(file);
    link.download = file.name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  };
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
    // Share as post: the server makes a square picture of the product and its price and writes the caption
    // (web/share.py). The caption goes on the clipboard, and the picture to the phone's share sheet, where
    // Instagram makes a post of it; a browser without one saves the picture instead.
    const post = box.querySelector("[data-share-post]");
    if (post && navigator.clipboard && window.isSecureContext && "fetch" in window) {
      post.hidden = false;
      post.querySelector("button").addEventListener("click", async () => {
        const where = box.dataset.sharePost;
        if (!where) return;
        status.textContent = post.dataset.shareMaking;
        try {
          const about = await fetch(where, { credentials: "same-origin", headers: { Accept: "application/json" } });
          if (!about.ok) throw new Error(String(about.status));
          const { caption, image, name } = await about.json();
          const picture = await fetch(image, { credentials: "same-origin" });
          if (!picture.ok) throw new Error(String(picture.status));
          const file = new File([await picture.blob()], `${name.replace(/[^\w]+/g, "-").replace(/^-|-$/g, "").toLowerCase()}.png`, { type: "image/png" });
          await navigator.clipboard.writeText(caption);
          if (navigator.canShare && navigator.canShare({ files: [file] })) {
            await navigator.share({ files: [file], title: name, text: caption });
            status.textContent = post.dataset.shareShared;
          } else {
            saveFile(file);
            status.textContent = post.dataset.shareSaved;
          }
        } catch (error) {
          if (error && error.name === "AbortError") { status.textContent = ""; return; }   // the sheet was closed
          status.textContent = post.dataset.shareFailed;
        }
      });
    }
    // Share to story: a tall picture of the product and its price (web/share.py). Instagram takes no story from a
    // web page, so the product's link goes on the clipboard for a link sticker, and the picture to the phone's share
    // sheet, where Instagram offers Stories. The button is a link that saves the picture: without script, or on a
    // browser that cannot share pictures (a computer), it does just that, with the link copied too.
    const story = box.querySelector("[data-share-story]");
    if (story) {
      const link = story.querySelector("a");
      link.addEventListener("click", async (event) => {
        const copied = navigator.clipboard && window.isSecureContext && link.dataset.shareStoryLink
          ? navigator.clipboard.writeText(link.dataset.shareStoryLink).then(() => true, () => false)
          : Promise.resolve(false);
        const probe = new File([""], "story.png", { type: "image/png" });
        if (!("fetch" in window) || !navigator.canShare || !navigator.canShare({ files: [probe] })) {
          status.textContent = "";
          copied.then((done) => { if (done) status.textContent = story.dataset.shareSaved; });
          return;   // the link saves the picture
        }
        event.preventDefault();
        status.textContent = story.dataset.shareMaking;
        let file = null;
        try {
          const picture = await fetch(link.href, { credentials: "same-origin" });
          if (!picture.ok) throw new Error(String(picture.status));
          file = new File([await picture.blob()], link.getAttribute("download") || "story.png", { type: "image/png" });
          status.textContent = (await copied) ? story.dataset.shareShared : "";
          await navigator.share({ files: [file] });
        } catch (error) {
          if (error && error.name === "AbortError") { status.textContent = ""; return; }   // the sheet was closed
          // The tap was too long ago by the time the picture came (a browser allows a sheet only just after one).
          if (file && error && error.name === "NotAllowedError") {
            saveFile(file);
            status.textContent = (await copied) ? story.dataset.shareSaved : "";
            return;
          }
          status.textContent = story.dataset.shareFailed;
        }
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
    // The post's text under its title, written for the card on the server (web/share.py).
    const reddit = { url: tagged(page, "reddit"), title: dialog.dataset.redditTitle.replace("{product}", name) };
    if (open.dataset.cardReddit) reddit.text = open.dataset.cardReddit;
    dialog.querySelector("[data-share-reddit]").href = "https://www.reddit.com/submit?" + new URLSearchParams(reddit).toString();
    box.querySelectorAll("[data-share-network]").forEach((item) => { item.dataset.shareCopy = tagged(page, item.dataset.shareNetwork); });
    const native = box.querySelector("[data-share-native]");
    if (native) native.dataset.shareUrl = tagged(page, "share_sheet");
    box.dataset.sharePost = page.pathname.replace(/\/?$/, "/") + "post/";
    const story = box.querySelector("[data-share-story]");
    if (story) {
      const link = story.querySelector("a");
      link.href = page.pathname.replace(/\/?$/, "/") + "story.png";
      link.setAttribute("download", `${page.pathname.split("/").filter(Boolean).pop()}-story.png`);
      link.dataset.shareStoryLink = tagged(page, "instagram_story");
      story.hidden = false;
    }
    box.querySelector("[data-share-link]").value = page.href;
    box.querySelector("[data-share-status]").textContent = "";
    dialog.showModal();
  });
  // Close, or a tap on the shaded page around the sheet, closes it; so does Escape, as for any dialog.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target.closest("[data-share-close]")) dialog.close();
  });
})();
