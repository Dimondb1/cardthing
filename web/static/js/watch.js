// Watchlist. The list lives in this browser (localStorage) and in the
// watchlist page's address, never on the server. Every Save link works
// without this file: it opens the watchlist page for that one product.
(() => {
  const STORE = "ripraptor.saved";   // shared with the swipe page
  const load = () => { try { return JSON.parse(localStorage.getItem(STORE)) || []; } catch (e) { return []; } };
  const store = (value) => { try { localStorage.setItem(STORE, JSON.stringify(value)); } catch (e) { /* private mode */ } };
  const slugOf = (item) => item.slug || (item.url || "").split("/").filter(Boolean).pop() || "";
  const pence = (value) => Math.round(parseFloat(String(value).replace(/[^0-9.]/g, "")) * 100);
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
  let saved = load();
  const slugs = () => saved.map(slugOf).filter(Boolean);
  const listUrl = (base) => { const s = slugs(); return s.length ? `${base}?p=${s.join(",")}` : base; };
  const page = document.querySelector("[data-watchlist]");

  function paintHeader() {
    document.querySelectorAll("[data-watch-link]").forEach((a) => { a.href = listUrl(a.dataset.watchLink); });
    document.querySelectorAll("[data-watch-count]").forEach((el) => { el.textContent = saved.length ? `(${saved.length})` : ""; });
  }

  function paintLinks() {
    const on = new Set(slugs());
    document.querySelectorAll("[data-watch]").forEach((a) => {
      const active = on.has(a.dataset.watch);
      a.classList.toggle("is-saved", active);
      a.setAttribute("role", "button");
      a.setAttribute("aria-pressed", active ? "true" : "false");
      const label = a.querySelector("[data-watch-label]");
      const inList = !!a.closest("[data-watchlist]");
      if (label) label.textContent = active ? (inList ? a.dataset.removeLabel : a.dataset.savedLabel) : a.dataset.saveLabel;
    });
  }

  function paintList() {
    if (!page) return;
    const params = new URLSearchParams(location.search);
    if (!params.get("p") && saved.length) { location.replace(listUrl(location.pathname)); return; }
    document.querySelectorAll("[data-watch-row]").forEach((row) => {
      const item = saved.find((s) => slugOf(s) === row.dataset.watchRow);
      const line = row.querySelector("[data-saved-for]");
      if (!line) return;
      if (!item || !item.price) { line.hidden = true; return; }
      line.hidden = false;
      line.textContent = fill(page.dataset.savedAt, { price: item.price });
      const now = row.querySelector("[data-now]");
      line.classList.remove("is-down", "is-up");
      if (now) {
        const diff = pence(now.dataset.now) - pence(item.price);
        if (diff < 0) line.classList.add("is-down");
        if (diff > 0) line.classList.add("is-up");
      }
    });
  }

  function toggle(a) {
    const slug = a.dataset.watch;
    if (slugs().includes(slug)) {
      saved = saved.filter((s) => slugOf(s) !== slug);
      const row = a.closest("[data-watch-row]");
      if (row) {
        row.hidden = true;
        if (saved.length) history.replaceState(null, "", listUrl(location.pathname));
        else location.replace(location.pathname);
      }
    } else {
      saved.push({
        id: Number(a.dataset.watchId) || 0, slug, name: a.dataset.watchName, url: `/products/${slug}/`,
        price: a.dataset.watchPrice ? `£${a.dataset.watchPrice}` : "", savedAt: new Date().toISOString().slice(0, 10),
      });
    }
    store(saved);
    paintLinks(); paintHeader(); paintList();
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-watch]");
    if (!a || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    toggle(a);
  });

  paintLinks(); paintHeader(); paintList();
})();
