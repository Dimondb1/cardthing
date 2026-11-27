// Story deals (web/story_deals.py): one tap on a deal's Share to story hands its picture to the phone's share sheet,
// where Instagram offers Stories, with the product's link copied for the story's link sticker. A web page cannot
// open Instagram's story editor or fill in the sticker itself. Without this script the button saves the picture, the
// link is there to copy and Update the picture reloads the page.
(() => {
  const list = document.querySelector("[data-deals]");
  if (!list) return;
  const words = list.dataset;
  const canShare = (() => {
    try {
      const probe = new File([""], "story.png", { type: "image/png" });
      return "fetch" in window && !!navigator.canShare && navigator.canShare({ files: [probe] });
    } catch (error) {
      return false;
    }
  })();
  const canCopy = !!(navigator.clipboard && window.isSecureContext);
  // Each picture is fetched once and kept, so the share sheet can open at once on a tap: a browser allows one only
  // just after a tap, and making a picture can take longer than that.
  const kept = new Map();
  const picture = (address, name) => {
    if (!kept.has(address)) {
      const coming = fetch(address, { credentials: "same-origin" })
        .then((response) => { if (!response.ok) throw new Error(String(response.status)); return response.blob(); })
        .then((blob) => new File([blob], name, { type: "image/png" }));
      coming.catch(() => kept.delete(address));
      kept.set(address, coming);
    }
    return kept.get(address);
  };
  const saveFile = (file) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(file);
    link.download = file.name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  };

  list.querySelectorAll("[data-deal]").forEach((card) => {
    const form = card.querySelector("[data-deal-form]");
    const shown = card.querySelector("[data-deal-picture]");
    const share = card.querySelector("[data-deal-share]");
    const said = card.querySelector("[data-deal-said]");
    const copyButton = card.querySelector("[data-deal-copy]");
    const name = card.dataset.file;
    const address = () => {
      const own = new URLSearchParams();
      ["badge", "line", "theme"].forEach((field) => {   // his own words, and the look he chose for the pictures
        const value = form.elements[field].value.trim();
        if (value) own.set(field, value);
      });
      const query = own.toString();
      return card.dataset.picture + (query ? `?${query}` : "");
    };
    // Once the card's picture has loaded, its copy for the share sheet comes from the browser's own store.
    const ready = () => { if (canShare) picture(share.getAttribute("href"), name).catch(() => {}); };
    if (shown.complete && shown.naturalWidth) ready();
    shown.addEventListener("load", ready);

    // The owner's own words: the picture follows them as they are typed, with no reload.
    let timer = 0;
    const refresh = () => {
      const next = address();
      if (share.getAttribute("href") === next) return;
      share.setAttribute("href", next);
      shown.src = next;
      said.textContent = "";
    };
    form.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(refresh, 700); });
    form.addEventListener("submit", (event) => { event.preventDefault(); clearTimeout(timer); refresh(); });

    if (canCopy && copyButton) {
      copyButton.hidden = false;
      copyButton.addEventListener("click", () => {
        navigator.clipboard.writeText(share.dataset.link).then(() => { said.textContent = words.copied; }, () => {});
      });
    }

    share.addEventListener("click", async (event) => {
      clearTimeout(timer);
      refresh();
      const copied = canCopy ? navigator.clipboard.writeText(share.dataset.link).then(() => true, () => false)
        : Promise.resolve(false);
      if (!canShare) {
        // The link saves the picture (a computer, or a browser that cannot share pictures).
        copied.then((done) => { said.textContent = done ? words.saved : words.savedOnly; });
        return;
      }
      event.preventDefault();
      said.textContent = words.making;
      let file = null;
      try {
        file = await picture(share.getAttribute("href"), name);
        said.textContent = (await copied) ? words.shared : "";
        await navigator.share({ files: [file] });
      } catch (error) {
        if (error && error.name === "AbortError") return;   // the sheet was closed
        // The tap was too long ago by the time the picture came: save it instead, and the next tap shares at once.
        if (file && error && error.name === "NotAllowedError") {
          saveFile(file);
          said.textContent = (await copied) ? words.saved : words.savedOnly;
          return;
        }
        said.textContent = words.failed;
      }
    });
  });
})();
