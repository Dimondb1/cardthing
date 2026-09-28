// Progressive enhancement only. Every page works without this file.

// Apply filters as soon as one changes. The "Show results" button is kept
// for visitors without JavaScript.
document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
  form.querySelectorAll("[data-autosubmit-hide]").forEach((el) => {
    el.hidden = true;
  });
  form.addEventListener("change", () => {
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else {
      form.submit();
    }
  });
});

// Results as you type. The form still submits normally to the full results
// page, so nothing here is required.
document.querySelectorAll("form[data-live-search]").forEach((form) => {
  const input = form.querySelector("input[name=q]");
  const panel = document.querySelector(`[data-live-panel="${input.id}"]`);
  if (!input || !panel) return;
  const status = panel.querySelector(".live__status");
  const list = panel.querySelector(".live__results");
  const endpoint = form.dataset.liveSearch;
  const wording = { checking: form.dataset.checking, none: form.dataset.none, all: form.dataset.all };
  let timer = null;
  let latest = 0;
  let index = null; // filled only when the endpoint returns everything (static preview)

  const fill = (text, query) => text.replace("{query}", query);

  const show = (query, results) => {
    list.textContent = "";
    if (!results.length) {
      status.textContent = fill(wording.none, query);
      panel.hidden = false;
      return;
    }
    status.textContent = "";
    results.forEach((r) => {
      const li = document.createElement("li");
      li.className = "live__item";
      const a = document.createElement("a");
      a.href = new URL(r.url, new URL(endpoint, location.href)).href;
      a.className = "live__link";
      const body = document.createElement("span");
      body.className = "live__body";
      const name = document.createElement("span");
      name.className = "live__name";
      name.textContent = r.name;
      const meta = document.createElement("span");
      meta.className = "live__meta";
      meta.textContent = r.meta;
      body.append(name, meta);
      const offer = document.createElement("span");
      offer.className = "live__offer";
      if (r.price) {
        const price = document.createElement("span");
        price.className = "price live__price";
        price.textContent = r.price;
        offer.append(price);
      }
      const stock = document.createElement("span");
      stock.className = `stock stock--${r.state}`;
      stock.textContent = r.stock;
      offer.append(stock);
      a.append(body, offer);
      li.append(a);
      list.append(li);
    });
    const more = document.createElement("li");
    more.className = "live__all";
    const link = document.createElement("a");
    link.href = `${form.action}?q=${encodeURIComponent(query)}`;
    link.textContent = fill(wording.all, query);
    more.append(link);
    list.append(more);
    panel.hidden = false;
  };

  const filterLocally = (query) => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return index.filter((r) => terms.every((t) => r.text.includes(" " + t))).slice(0, 6);
  };

  const run = async (query) => {
    const id = ++latest;
    if (!query) {
      panel.hidden = true;
      list.textContent = "";
      status.textContent = "";
      return;
    }
    if (index) {
      show(query, filterLocally(query));
      return;
    }
    status.textContent = wording.checking;
    panel.hidden = false;
    try {
      const sep = endpoint.includes("?") ? "&" : "?";
      const response = await fetch(`${endpoint}${sep}q=${encodeURIComponent(query)}`, { headers: { Accept: "application/json" } });
      const data = await response.json();
      if (id !== latest) return;
      if (data.all) {
        index = data.results;
        show(query, filterLocally(query));
        return;
      }
      show(query, data.results);
    } catch (err) {
      if (id === latest) panel.hidden = true;
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => run(input.value.trim()), 180);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      panel.hidden = true;
    }
  });
  document.addEventListener("click", (event) => {
    if (!form.contains(event.target) && !panel.contains(event.target)) {
      panel.hidden = true;
    }
  });
  input.addEventListener("focus", () => {
    if (list.children.length) panel.hidden = false;
  });
});

// Back arrow: go back in history when we came from another CardScout page.
document.querySelectorAll("[data-back]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (document.referrer && new URL(document.referrer).origin === location.origin && history.length > 1) {
      event.preventDefault();
      history.back();
    }
  });
});
