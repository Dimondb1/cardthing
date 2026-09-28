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

  let selected = -1;
  const items = () => Array.from(list.querySelectorAll(".live__item, .live__all"));
  const select = (index) => {
    const all = items();
    selected = all.length ? Math.max(-1, Math.min(index, all.length - 1)) : -1;
    all.forEach((li, i) => {
      li.setAttribute("aria-selected", i === selected ? "true" : "false");
      li.classList.toggle("live__all--selected", i === selected && li.classList.contains("live__all"));
    });
    input.setAttribute("aria-activedescendant", selected >= 0 ? all[selected].id : "");
  };
  const open = (isOpen) => {
    panel.hidden = !isOpen;
    input.setAttribute("aria-expanded", isOpen ? "true" : "false");
    if (!isOpen) select(-1);
  };

  const show = (query, results) => {
    list.textContent = "";
    select(-1);
    if (!results.length) {
      status.textContent = fill(wording.none, query);
      open(true);
      return;
    }
    status.textContent = "";
    results.forEach((r, i) => {
      const li = document.createElement("li");
      li.className = "live__item";
      li.id = `${input.id}-option-${i}`;
      li.setAttribute("role", "option");
      li.style.setProperty("--i", i);
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
    more.id = `${input.id}-option-all`;
    more.setAttribute("role", "option");
    const link = document.createElement("a");
    link.href = `${form.action}?q=${encodeURIComponent(query)}`;
    link.textContent = fill(wording.all, query);
    more.append(link);
    list.append(more);
    open(true);
  };

  const filterLocally = (query) => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return index.filter((r) => terms.every((t) => r.text.includes(" " + t))).slice(0, 6);
  };

  const run = async (query) => {
    const id = ++latest;
    if (!query) {
      open(false);
      list.textContent = "";
      status.textContent = "";
      return;
    }
    if (index) {
      show(query, filterLocally(query));
      return;
    }
    status.textContent = wording.checking;
    open(true);
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
      if (id === latest) open(false);
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => run(input.value.trim()), 180);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      open(false);
      return;
    }
    if (panel.hidden) return;
    if (event.key === "ArrowDown") { event.preventDefault(); select(selected + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); select(selected - 1); }
    else if (event.key === "Enter" && selected >= 0) {
      event.preventDefault();
      const link = items()[selected].querySelector("a");
      if (link) location.href = link.href;
    }
  });
  document.addEventListener("click", (event) => {
    if (!form.contains(event.target) && !panel.contains(event.target)) open(false);
  });
  input.addEventListener("focus", () => {
    if (list.children.length) open(true);
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

// One-time reveals for lists below the fold. Nothing replays on scroll.
if ("IntersectionObserver" in window && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const targets = document.querySelectorAll(".saving, .card, .drop, .trending__item");
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -8% 0px" });
  targets.forEach((el, i) => {
    const rect = el.getBoundingClientRect();
    if (rect.top < window.innerHeight) return; // already on screen: no delay for the first view
    el.classList.add("reveal");
    observer.observe(el);
  });
}

// Product images settle in once loaded rather than popping.
document.querySelectorAll(".product-image img").forEach((img) => {
  if (img.complete) img.classList.add("is-loaded");
  else img.addEventListener("load", () => img.classList.add("is-loaded"), { once: true });
});
