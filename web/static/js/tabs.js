// Stock tabs on game and set pages: swipe or tap between In stock, Pre-orders and Out of stock (only
// the tabs with products are there; a page with one list has no tabs and does not load this file).
// Without this file each tab is a link to its own page, which holds only that tab's list. With it,
// the other lists are fetched one after the other once the page is idle and slotted in beside the
// first, so a swipe moves between them at once. During a swipe only the browser's own scrolling
// moves; the tab, the address and the height change once it settles.
(() => {
  const root = document.querySelector("[data-stock]");
  const track = root && root.querySelector(".stock-panes");
  const nav = root && root.querySelector(".stock-tabs");
  if (!track || !nav || !("fetch" in window)) return;
  // The tab the page opens on needs no tab in its address.
  const opening = root.dataset.opening;
  const tabs = Array.from(nav.querySelectorAll("a[data-tab]"));
  const keys = tabs.map((a) => a.dataset.tab);
  const tabInput = root.querySelector("[data-tab-input]");
  const clearLink = root.querySelector("[data-tab-link]");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  const pane = (key) => track.querySelector(`.stock-pane[data-tab="${key}"]`);
  let current = Math.max(0, tabs.findIndex((a) => a.getAttribute("aria-current") === "page"));
  // The list the page came with keeps its own address, page number and all.
  const urls = tabs.map((a, i) => (i === current ? location.href : a.href));
  const loaded = new Set([keys[current]]);
  const pending = new Map();
  const sizes = "ResizeObserver" in window ? new ResizeObserver(() => fit()) : null;

  function fit() {
    // As tall as the list in view, not the longest one.
    const shown = pane(keys[current]);
    if (shown) track.style.height = `${shown.offsetHeight}px`;
  }

  function mark() {
    keys.forEach((key, i) => {
      const item = pane(key);
      if (item) item.inert = i !== current;
    });
  }

  function select(index) {
    current = index;
    const key = keys[index];
    tabs.forEach((a, i) => (i === index ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    mark();
    history.replaceState(history.state, "", urls[index]);
    if (tabInput) tabInput.value = key;
    if (clearLink) {
      const url = new URL(clearLink.href, location.href);
      if (key === opening) url.searchParams.delete("tab");
      else url.searchParams.set("tab", key);
      clearLink.href = url.pathname + url.search;
    }
    fit();
    // A list swiped to starts at its top: bring that into view when the page is scrolled past it.
    const above = track.getBoundingClientRect().top - nav.offsetHeight;
    if (above < 0) window.scrollBy(0, above);
    load(key);
  }

  function load(key) {
    if (loaded.has(key)) return Promise.resolve();
    if (pending.has(key)) return pending.get(key);
    const holder = pane(key);
    const job = fetch(holder.dataset.src, { credentials: "same-origin" })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.text();
      })
      .then((html) => {
        const parsed = document.createElement("template");
        parsed.innerHTML = html.trim();
        const fresh = parsed.content.querySelector(".stock-pane");
        if (!fresh) throw new Error("no list");
        holder.replaceWith(fresh);
        loaded.add(key);
        if (sizes) sizes.observe(fresh);
        mark();
        document.dispatchEvent(new CustomEvent("ripraptor:content", { detail: fresh }));
        fit();
      })
      .catch(() => {
        // Not fetched: the tab's own page still has the list.
        const note = holder.querySelector(".stock-pane__loading");
        if (note) {
          const link = document.createElement("a");
          link.href = urls[keys.indexOf(key)];
          link.textContent = tabs[keys.indexOf(key)].querySelector(".stock-tabs__label").textContent;
          note.replaceChildren(link);
        }
      })
      .finally(() => pending.delete(key));
    pending.set(key, job);
    return job;
  }

  // Every list side by side, the one asked for in view.
  keys.forEach((key) => {
    const item = pane(key);
    item.hidden = false;
    if (sizes) sizes.observe(item);
  });
  track.classList.add("is-swipe");
  track.scrollLeft = current * track.clientWidth;
  mark();
  fit();

  tabs.forEach((a, index) => a.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
    event.preventDefault();
    track.scrollTo({ left: index * track.clientWidth, behavior: reduce.matches ? "auto" : "smooth" });
    if (index !== current) select(index);
  }));

  // Only a visitor's own swipe changes the tab. Placing the list on load can fire scrollend before the snap settles
  // (straight away when motion is reduced), and that must not move a Pre-orders link onto another list.
  let touched = false;
  ["pointerdown", "touchstart", "wheel", "keydown"].forEach((type) => track.addEventListener(type, () => { touched = true; }, { passive: true }));

  function settle() {
    if (!touched) return;
    const index = Math.round(track.scrollLeft / track.clientWidth);
    if (index !== current && index >= 0 && index < keys.length) select(index);
  }
  if ("onscrollend" in window) {
    track.addEventListener("scrollend", settle);
  } else {
    let wait;
    track.addEventListener("scroll", () => { clearTimeout(wait); wait = setTimeout(settle, 120); }, { passive: true });
  }
  window.addEventListener("resize", () => {
    track.scrollLeft = current * track.clientWidth;
    fit();
  });

  // The other lists, nearest first, one at a time so the server answers one request at once.
  const idle = window.requestIdleCallback || ((run) => setTimeout(run, 300));
  idle(() => {
    keys.filter((key) => !loaded.has(key))
      .sort((a, b) => Math.abs(keys.indexOf(a) - current) - Math.abs(keys.indexOf(b) - current))
      .reduce((chain, key) => chain.then(() => load(key)), Promise.resolve());
  });
})();
