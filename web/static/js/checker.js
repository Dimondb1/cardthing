// The Fair Price Checker and guide pages (guides/): products offered as you type, Copy link and Print. Each only
// speeds things up: without script the checker's form lists products to choose from, and the browser prints.
(() => {
  const api = "/api/search/";

  document.querySelectorAll("[data-checker]").forEach((form) => {
    const field = form.querySelector("[data-checker-q]");
    const chosen = form.querySelector("[data-checker-product]");
    const list = form.querySelector("[data-checker-list]");
    if (!field || !chosen || !list) return;
    let timer = 0;
    let asked = null;

    const close = () => { list.hidden = true; list.replaceChildren(); };
    const choose = (result) => {
      const slug = result.url.split("/").filter(Boolean).pop();
      chosen.value = slug;
      chosen.disabled = false;
      field.value = result.name;
      close();
      form.querySelector("input[name=price]")?.focus();
    };
    const show = (results) => {
      list.replaceChildren(...results.map((result) => {
        const item = document.createElement("li");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "checker__option";
        const name = document.createElement("span");
        name.className = "checker__option-name";
        name.textContent = result.name;
        const meta = document.createElement("span");
        meta.className = "checker__option-meta";
        meta.textContent = result.meta;
        button.append(name, meta);
        button.addEventListener("click", () => choose(result));
        item.append(button);
        return item;
      }));
      list.hidden = !results.length;
    };

    field.addEventListener("input", () => {
      chosen.disabled = true;
      chosen.value = "";
      clearTimeout(timer);
      const words = field.value.trim();
      if (words.length < 2) { close(); return; }
      timer = setTimeout(() => {
        asked?.abort();
        asked = new AbortController();
        fetch(`${api}?q=${encodeURIComponent(words)}`, { signal: asked.signal })
          .then((response) => (response.ok ? response.json() : { results: [] }))
          .then((data) => { if (field.value.trim() === words) show(data.results || []); })
          .catch(() => {});
      }, 150);
    });
    field.addEventListener("keydown", (event) => { if (event.key === "Escape") close(); });
  });

  document.querySelectorAll("[data-guide-share]").forEach((box) => {
    const button = box.querySelector("[data-copy-link]");
    const status = box.querySelector("[data-copy-status]");
    if (!button || !navigator.clipboard) return;
    button.hidden = false;
    button.addEventListener("click", () => {
      navigator.clipboard.writeText(box.dataset.shareUrl).then(() => {
        if (status) status.textContent = status.dataset.copied;
      }).catch(() => {});
    });
  });

  document.querySelectorAll("[data-print]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", () => window.print());
  });
})();
