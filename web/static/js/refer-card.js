// The refer a friend card: now and then, while the keyring offer is open, a small card invites the visitor to
// refer a friend. Never on a browser's first page, on about one page in five after that, at most once a day,
// and only after a few seconds or once the visitor has scrolled halfway. "No thanks" means never again in this
// browser, and so does asking for a link (the pages after the form load this file with data-refer-asked).
// One card at a time: it waits while the analytics or home screen card is up. Nothing here talks to the server.
// Without JavaScript the card never shows and the footer link is still there.
(() => {
  const DONE = "ripraptor.refer";          // "done" once dismissed or once a link was asked for
  const PAGES = "ripraptor.refer.pages";   // pages seen with the card on offer, so the first is never interrupted
  const SHOWN = "ripraptor.refer.shown";   // the day it last showed, so at most once a day
  const CHANCE = 0.2;
  const DELAY = 8000;
  const get = (key) => {
    for (const name of ["localStorage", "sessionStorage"]) {
      try { const value = window[name].getItem(key); if (value !== null) return value; } catch (e) { /* storage blocked */ }
    }
    return null;
  };
  const set = (key, value) => {
    try { localStorage.setItem(key, value); return; } catch (e) { /* private mode or full */ }
    try { sessionStorage.setItem(key, value); } catch (e) { /* this page only, then */ }
  };
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  const script = document.currentScript;
  if (script && script.hasAttribute("data-refer-asked")) { set(DONE, "done"); return; }
  const card = document.querySelector("[data-refer-card]");
  if (!card || get(DONE) === "done") return;

  let pages = 0;
  try { pages = (parseInt(localStorage.getItem(PAGES), 10) || 0) + 1; localStorage.setItem(PAGES, String(Math.min(pages, 99))); } catch (e) { return; }
  if (pages < 2 || get(SHOWN) === today() || Math.random() >= CHANCE) return;

  const other = () => document.querySelector("[data-consent]:not([hidden]), [data-install]:not([hidden])");
  const onKey = (e) => { if (e.key === "Escape" && !e.defaultPrevented) hide(); };
  let timer = 0;
  let shown = false;
  const hide = () => {
    const hadFocus = card.contains(document.activeElement);
    card.classList.remove("is-shown");
    card.hidden = true;
    removeEventListener("keydown", onKey);
    if (hadFocus) {
      const main = document.getElementById("main");
      if (main) { main.setAttribute("tabindex", "-1"); main.focus({ preventScroll: true }); }
    }
  };
  const show = () => {
    clearTimeout(timer);
    removeEventListener("scroll", onScroll);
    if (shown || other() || document.hidden) return;   // another card is up: try again on a later page
    shown = true;
    set(SHOWN, today());
    card.hidden = false;
    requestAnimationFrame(() => card.classList.add("is-shown"));
    addEventListener("keydown", onKey);
  };
  const onScroll = () => {
    const root = document.documentElement;
    if (scrollY + innerHeight >= root.scrollHeight / 2) show();
  };

  card.querySelector("[data-refer-card-dismiss]").addEventListener("click", () => { set(DONE, "done"); hide(); });
  const again = document.querySelector("[data-consent-open]");
  if (again) again.addEventListener("click", () => { if (shown) hide(); });
  timer = setTimeout(show, DELAY);
  addEventListener("scroll", onScroll, { passive: true });
})();
