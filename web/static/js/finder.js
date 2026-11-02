// The home page's finder (views.home_finder): its products follow each choice at once, without reloading the page.
// Without this script every tab, shortcut and chip is a plain link and the menus a form with a Show products button,
// so the finder works the same, a page at a time.
//
// A choice fetches the finder alone (?pane=finder, never counted as a page view) and swaps it in; the address follows,
// so the list can be shared or reloaded, and Back returns to the choices before. Only the finder below the search box
// is replaced: the search box keeps its words and focus. While a list loads the old one dims (opacity only), so a tap
// is seen to land; with reduced motion it changes at once.
(() => {
  const finder = document.querySelector("[data-finder]");
  const body = finder && finder.querySelector("[data-finder-body]");
  if (!body || !window.fetch || !window.URLSearchParams) return;
  const said = finder.querySelector("[data-finder-said]");
  const narrow = window.matchMedia("(max-width: 47.99rem)");
  let pending = null;

  finder.classList.add("is-live");

  // On a phone the menus fold under Filters until asked for; on a wider screen they always show.
  const fold = (keepOpen) => {
    const panel = body.querySelector("[data-finder-panel]");
    if (panel) panel.open = narrow.matches ? Boolean(keepOpen) : true;
  };
  fold(false);
  narrow.addEventListener("change", () => fold(false));

  // The address a form's choices make: empty menus and the default availability (in stock) are left out.
  const addressOf = (form) => {
    const params = new URLSearchParams();
    for (const [name, value] of new FormData(form)) {
      if (value === "" || (name === "stock" && value === "in")) continue;
      params.append(name, value);
    }
    const query = params.toString();
    return form.getAttribute("action") + (query ? "?" + query : "");
  };

  const load = async (address, push) => {
    if (pending) pending.abort();
    pending = new AbortController();
    const results = body.querySelector("[data-finder-results]");
    if (results) results.setAttribute("aria-busy", "true");
    const panel = body.querySelector("[data-finder-panel]");
    const wasOpen = panel ? panel.open : false;
    const focused = document.activeElement;
    const focusName = focused && body.contains(focused) ? (focused.name || focused.textContent.trim()) : "";
    const url = new URL(address, window.location.href);
    url.searchParams.set("pane", "finder");
    try {
      const response = await fetch(url, { signal: pending.signal, credentials: "same-origin", headers: { Accept: "text/html" } });
      if (response.ok === false) throw new Error(String(response.status));
      body.innerHTML = await response.text();
    } catch (error) {
      if (error.name === "AbortError") return;
      window.location.href = address;   // the plain page, as without the script
      return;
    }
    pending = null;
    fold(wasOpen);
    const clean = new URL(address, window.location.href);
    const state = { finder: true };
    if (push) history.pushState(state, "", clean.pathname + clean.search);
    else history.replaceState(state, "", clean.pathname + clean.search);
    // Focus goes back to the menu or tab that was used, so a keyboard keeps its place.
    if (focusName) {
      const again = body.querySelector(`select[name="${CSS.escape(focusName)}"]`)
        || Array.from(body.querySelectorAll("[data-finder-link]")).find((link) => link.textContent.trim() === focusName);
      if (again) again.focus({ preventScroll: true });
    }
    const count = body.querySelector(".finder__count");
    if (said) said.textContent = count ? count.textContent : "";
    document.dispatchEvent(new CustomEvent("ripraptor:content", { detail: body }));
  };

  body.addEventListener("change", (event) => {
    const form = event.target.closest("[data-finder-form]");
    if (form && event.target.matches("select")) load(addressOf(form), false);
  });
  body.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-finder-form]");
    if (form === null) return;
    event.preventDefault();
    load(addressOf(form), true);
  });
  body.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-finder-link]");
    if (link === null || event.metaKey || event.ctrlKey || event.shiftKey || event.button > 0) return;
    event.preventDefault();
    const menu = link.closest("details.finder__menu");
    if (menu) menu.open = false;
    load(link.href, true);
  });
  window.addEventListener("popstate", (event) => {
    if (event.state && event.state.finder) load(window.location.href, false);
  });
  // The first page's own state, so Back after a choice returns to it.
  history.replaceState({ finder: true }, "", window.location.pathname + window.location.search);
})();
