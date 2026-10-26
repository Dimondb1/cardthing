// Filters, quick filters and Clear filters on game, set and search pages change the list in place. Each is a
// plain link or a GET form, so without this file (or when a fetch fails) it loads the page it names. With it,
// that page is fetched and its results (the part of the page under data-browse) take the place of the ones on
// screen, under a short cross-fade where the browser has view transitions and motion is not reduced: nothing
// scrolls away, the folded panel stays open, the address follows and Back goes through the lists seen. The
// scripts that enhance the list hear ripraptor:content (images, Save links, the filter form) and ripraptor:page
// (the stock tabs) and set the new markup up. Nothing here is required.
(() => {
  const OPEN = "rr-filters-open";   // the folded panel, kept open across a full load of the same page
  let root = document.querySelector("[data-browse]");
  if (!root) return;

  // A page loaded in full (no fetch, or one that failed) opens the panel the change was made in, this once.
  const panel = () => root.querySelector("[data-filter-panel]");
  try {
    if (sessionStorage.getItem(OPEN) === location.pathname && panel()) panel().open = true;
    sessionStorage.removeItem(OPEN);
  } catch (e) { /* storage off */ }
  const remember = () => {
    try { if (panel() && panel().open) sessionStorage.setItem(OPEN, location.pathname); } catch (e) { /* storage off */ }
  };

  if (!("fetch" in window) || !("DOMParser" in window) || !history.pushState || !("AbortController" in window)) return;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  let controller = null;
  // The list the page opened with is an entry Back can return to, as every list after it is.
  history.replaceState({ ...(history.state || {}), browse: true }, "", location.href);

  // The address a filter form names: the page's, with its choices as the browser itself would send them.
  const target = (form) => {
    const url = new URL(form.getAttribute("action") || location.pathname, location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    return url;
  };

  function show(fresh, title) {
    const open = !!(panel() && panel().open);
    const focused = document.activeElement && document.activeElement.id;
    const apply = () => {
      const details = fresh.querySelector("[data-filter-panel]");
      if (details) details.open = open;
      root.replaceWith(fresh);
      root = fresh;
      if (title) document.title = title;
      // The select that was changed keeps the focus, so the next choice is a keypress away.
      const again = focused && document.getElementById(focused);
      if (again && typeof again.focus === "function") again.focus({ preventScroll: true });
      document.dispatchEvent(new CustomEvent("ripraptor:content", { detail: fresh }));
      document.dispatchEvent(new CustomEvent("ripraptor:page", { detail: fresh }));
    };
    if (typeof document.startViewTransition === "function" && !reduce.matches) document.startViewTransition(apply);
    else apply();
  }

  function load(url, push) {
    if (controller) controller.abort();
    controller = new AbortController();
    const { signal } = controller;
    root.setAttribute("aria-busy", "true");
    fetch(url, { credentials: "same-origin", signal })
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.text().then((html) => [html, response.url || url]);
      })
      .then(([html, landed]) => {
        const page = new DOMParser().parseFromString(html, "text/html");
        const fresh = page.querySelector("[data-browse]");
        if (!fresh) throw new Error("no list");
        if (push) history.pushState({ browse: true }, "", landed);
        show(fresh, page.title);
      })
      .catch((error) => {
        if (error.name === "AbortError") return;
        // Not fetched: the page the form or link named, loaded in full as it would have been.
        remember();
        location.assign(url);
      })
      .finally(() => { if (!signal.aborted) root.removeAttribute("aria-busy"); });
  }

  // A change in the filter form (site.js submits it) fetches the page it names instead.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (event.defaultPrevented || !root.contains(form) || !form.matches("form[data-autosubmit]")) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
    event.preventDefault();
    load(target(form).href, true);
  });

  // So does a link to this page with other choices: a quick filter, Clear filters, the link under an empty
  // list. Another page's link, a page of results (the top of a new page should be in view) and a link the
  // stock tabs or Save already took are left alone.
  document.addEventListener("click", (event) => {
    const link = event.target.closest && event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!root.contains(link) || link.target || link.closest(".pagination")) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname || url.hash || url.search === location.search) return;
    event.preventDefault();
    load(url.href, true);
  });

  // Back and Forward move through the lists seen, fetched again so the prices are the page's own.
  window.addEventListener("popstate", (event) => {
    if (event.state && event.state.browse) load(location.href, false);
  });
})();
