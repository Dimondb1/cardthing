// Progressive enhancement only. Every page works without this file.

// Apply filters as soon as one changes. The "Show results" button is kept
// for visitors without JavaScript.
// A press of a Compare prices link (data-compare: the product page's button, a card's "Compare N shops", a row
// card) is counted on our own server beside the clicks to shops, so the dashboard can tell Buy now, Compare
// prices and a shop's row apart. Nothing about the visitor is sent: where the link was and on which page.
document.addEventListener("click", (event) => {
  const link = event.target.closest && event.target.closest("[data-compare]");
  const { note, page } = document.body.dataset;
  if (!link || !note || !page) return;
  const query = new URLSearchParams({ what: "compare", where: link.dataset.compare, page });
  try { fetch(`${note}?${query}`, { keepalive: true }).catch(() => {}); } catch (e) { /* nothing to do */ }
});

// A page the browser fetched or rendered ahead of a tap (the speculation rules in base.html) was not counted
// when fetched: it is counted once it is shown, so the dashboard counts visits, not the browser's guesses.
{
  const entry = performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
  const ahead = document.prerendering || (entry && entry.deliveryType === "navigational-prefetch");
  const { note } = document.body.dataset;
  if (ahead && note) {
    const count = () => { try { fetch(`${note}?what=view&path=${encodeURIComponent(location.pathname)}`, { keepalive: true }).catch(() => {}); } catch (e) { /* nothing to do */ } };
    if (document.prerendering) document.addEventListener("prerenderingchange", count, { once: true });
    else count();
  }
}

// Apply filters as soon as one changes; the "Show results" button is kept for visitors without JavaScript. On
// game, set and search pages browse.js answers the submit by changing the list in place. Run again for a form
// those pages swap in later (ripraptor:content), once per form.
function autosubmit(root) {
  root.querySelectorAll("form[data-autosubmit]:not([data-autosubmit-ready])").forEach((form) => {
    form.dataset.autosubmitReady = "";
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
}
autosubmit(document);

// A filter pill is as wide as its chosen option, not its widest one: "Type" stays short until a type is picked.
// Without this the browser sizes each select to its longest option and the pills take a row each on a phone.
const fitPills = () => {
  document.querySelectorAll(".pill select").forEach((select) => {
    const option = select.options[select.selectedIndex];
    if (!option) return;
    const probe = document.createElement("span");
    probe.textContent = option.text;
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:nowrap;font:inherit";
    select.parentNode.append(probe);
    select.style.width = `calc(${Math.ceil(probe.getBoundingClientRect().width)}px + 1.2rem + 4px)`;
    probe.remove();
  });
};
fitPills();
if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitPills);

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

  // Before anything is typed: the searches this browser made before, newest first.
  const recentSearches = () => { try { return JSON.parse(localStorage.getItem("ripraptor.searches")) || []; } catch (e) { return []; } };
  const showRecent = () => {
    const recent = recentSearches();
    list.textContent = "";
    select(-1);
    if (!recent.length || !form.dataset.recent) { open(false); status.textContent = ""; return; }
    status.textContent = form.dataset.recent;
    recent.forEach((term, i) => {
      const li = document.createElement("li");
      li.className = "live__item live__item--recent";
      li.id = `${input.id}-option-${i}`;
      li.setAttribute("role", "option");
      li.style.setProperty("--i", i);
      const a = document.createElement("a");
      a.href = `${form.action}?q=${encodeURIComponent(term)}`;
      a.className = "live__link";
      a.textContent = term;
      li.append(a);
      list.append(li);
    });
    open(true);
  };

  const run = async (query) => {
    const id = ++latest;
    if (!query) {
      showRecent();
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
    else if (!input.value.trim()) showRecent();
  });
});

// A press on a buy button: its raptor snaps once (.is-chomping in site.css) as the shop opens in a new tab, so the tap
// is felt. Only a class changes; a press during the snap leaves it to finish.
document.addEventListener("click", (event) => {
  const button = event.target.closest && event.target.closest(".buy-now, .button--buy, .buy-bar__buy");
  if (button) button.classList.add("is-chomping");
});
document.addEventListener("animationend", (event) => {
  if (event.animationName === "chomp") event.target.closest(".is-chomping")?.classList.remove("is-chomping");
});

// One-time reveals for lists below the fold. Nothing replays on scroll.
if ("IntersectionObserver" in window && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  // Not a list's cards: a list is there as soon as the page is, so a filter change or a scroll never waits on it.
  const targets = document.querySelectorAll(".saving, .drop, .trending__item");
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

// Product images settle in once loaded rather than popping, in the page and in lists added later
// (the stock tabs send ripraptor:content with the new list).
// A resized copy the shop's CDN cannot make (the shop replaced the file) falls back to the address we hold.
function fullSize(img) {
  const full = img.dataset.full;
  if (!full || img.getAttribute("src") === full) return;
  img.removeAttribute("srcset");
  img.removeAttribute("sizes");
  img.src = full;
}
function settleImages(root) {
  root.querySelectorAll(".product-image img").forEach((img) => {
    if (img.complete && img.naturalWidth === 0) fullSize(img);
    else if (img.complete) img.classList.add("is-loaded");
    else img.addEventListener("load", () => img.classList.add("is-loaded"), { once: true });
    img.addEventListener("error", () => fullSize(img));
  });
}
settleImages(document);
document.addEventListener("ripraptor:content", (event) => {
  settleImages(event.detail);
  autosubmit(event.detail);
  fitPills();
});
