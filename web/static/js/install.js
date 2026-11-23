// Offer the site for the phone's home screen. The card asks on a phone after a few pages; a Get the app link in the
// footer is there whenever the site can be added and is not already, on any screen. Each step is counted in
// Insights with no record of who.
//
// What this browser remembers (ripraptor.install): "installed" once the site has been opened from its icon or added,
// or "not-now:<date>" after Not now, when the card waits WAIT_DAYS before it asks again. Anything else, the "done" an
// earlier version kept for both included, counts as no answer yet. The owner, 23 Nov 2026: the site "didn't ask me
// to install the app after I had uninstalled it", because "installed" was kept for good. Android Chrome now says when
// the app is not on the phone (it offers to install only then), and an "installed" kept from before is forgotten.
// iPhone Safari offers nothing and keeps a home screen app's storage apart from its own, so there the card explains
// the Share menu, and the footer link opens it whenever asked.
// A page the browser rendered ahead of a tap (the speculation rules in base.html) waits until it is shown: nothing is
// remembered or counted for a page nobody opened.
function offer() {
  const card = document.querySelector("[data-install]");
  const link = document.querySelector("[data-install-open]");
  if (!card) return;
  const STATE = "ripraptor.install";
  const VISITS = "ripraptor.pages";     // pages seen in this browser, so the first page is never interrupted
  const ASK_AFTER = 3;
  const WAIT_DAYS = 30;
  const get = (key) => { try { return localStorage.getItem(key); } catch (e) { return null; } };
  const set = (key, value) => { try { localStorage.setItem(key, value); } catch (e) { /* private mode */ } };
  const forget = (key) => { try { localStorage.removeItem(key); } catch (e) { /* private mode */ } };
  const note = (what) => { try { fetch(`${card.dataset.note}?what=${what}`, { keepalive: true }).catch(() => {}); } catch (e) { /* nothing to do */ } };
  const today = new Date().toISOString().slice(0, 10);

  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (standalone) {
    set(STATE, "installed");
    if (get("ripraptor.opened") !== today) { set("ripraptor.opened", today); note("opened"); }
    return;
  }
  const installed = () => get(STATE) === "installed";
  const waiting = () => {
    const since = /^not-now:(\d{4}-\d{2}-\d{2})$/.exec(get(STATE) || "");
    return !!since && Date.now() - Date.parse(since[1]) < WAIT_DAYS * 86400000;
  };
  const pages = (parseInt(get(VISITS), 10) || 0) + 1;
  set(VISITS, String(pages));
  const due = () => pages >= ASK_AFTER && matchMedia("(max-width: 47.99rem)").matches && !installed() && !waiting();

  const hide = () => { card.classList.remove("is-shown"); card.hidden = true; };
  const added = () => { set(STATE, "installed"); hide(); if (link) link.hidden = true; note("added"); };
  const notNow = () => { set(STATE, `not-now:${today}`); hide(); note("dismissed"); };
  // ``asked``: opened from the footer link, so it shows whatever else is on screen and however recently Not now was
  // pressed. Otherwise the analytics card asks first, and the refer a friend card keeps its turn once up.
  const show = (asked) => {
    if (!card.hidden) return;
    if (!asked && document.querySelector("[data-consent]:not([hidden]), [data-refer-card]:not([hidden])")) return;
    card.hidden = false;
    requestAnimationFrame(() => card.classList.add("is-shown"));
    note("shown");
  };
  card.querySelector("[data-install-dismiss]").addEventListener("click", notNow);
  addEventListener("appinstalled", added);

  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  if (ios) {
    card.querySelector("[data-install-android]").hidden = true;
    card.querySelector("[data-install-ios]").hidden = false;
    card.querySelector("[data-install-go]").hidden = true;
    if (link) {
      link.hidden = false;
      link.addEventListener("click", () => show(true));
    }
    if (due()) show(false);
    return;
  }
  // Android (and a computer's Chrome or Edge): the browser's own install question, which can be asked once.
  let prompt = null;
  const ask = async () => {
    if (!prompt) return;
    const asking = prompt;
    prompt = null;
    if (link) link.hidden = true;   // the browser offers it again on a later page
    asking.prompt();
    const choice = await asking.userChoice.catch(() => ({ outcome: "dismissed" }));
    if (choice.outcome === "accepted") added(); else notNow();
  };
  addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    prompt = event;
    // Chrome offers this only when the app is not on this device: an "installed" kept from before it was removed is
    // out of date.
    if (installed()) forget(STATE);
    if (link) link.hidden = false;
    if (due()) show(false);
  });
  card.querySelector("[data-install-go]").addEventListener("click", ask);
  if (link) link.addEventListener("click", ask);
}
if (document.prerendering) document.addEventListener("prerenderingchange", offer, { once: true });
else offer();
