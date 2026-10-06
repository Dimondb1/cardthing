// Offer the site for the phone's home screen: once, on a phone, after a
// few pages. Dismissed or added, it never asks again in this browser.
// Android Chrome gives us its install prompt; iPhone Safari has none, so
// there the card explains the Share menu instead. Each step is counted
// in Insights with no record of who.
(() => {
  const card = document.querySelector("[data-install]");
  if (!card) return;
  const DONE = "ripraptor.install";     // "done" once answered or installed
  const VISITS = "ripraptor.pages";     // pages seen in this browser, so the first page is never interrupted
  const ASK_AFTER = 3;
  const get = (key) => { try { return localStorage.getItem(key); } catch (e) { return null; } };
  const set = (key, value) => { try { localStorage.setItem(key, value); } catch (e) { /* private mode */ } };
  const note = (what) => { try { fetch(`${card.dataset.note}?what=${what}`, { keepalive: true }).catch(() => {}); } catch (e) { /* nothing to do */ } };

  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (standalone) {
    set(DONE, "done");
    const today = new Date().toISOString().slice(0, 10);
    if (get("ripraptor.opened") !== today) { set("ripraptor.opened", today); note("opened"); }
    return;
  }
  if (get(DONE) === "done") return;
  const pages = (parseInt(get(VISITS), 10) || 0) + 1;
  set(VISITS, String(pages));
  if (pages < ASK_AFTER || !matchMedia("(max-width: 47.99rem)").matches) return;

  const finish = (what) => { set(DONE, "done"); card.hidden = true; note(what); };
  const show = () => { card.hidden = false; requestAnimationFrame(() => card.classList.add("is-shown")); note("shown"); };
  card.querySelector("[data-install-dismiss]").addEventListener("click", () => finish("dismissed"));
  addEventListener("appinstalled", () => finish("added"));

  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  if (ios) {
    card.querySelector("[data-install-android]").hidden = true;
    card.querySelector("[data-install-ios]").hidden = false;
    card.querySelector("[data-install-go]").hidden = true;
    show();
    return;
  }
  let prompt = null;
  addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    prompt = e;
    show();
  });
  card.querySelector("[data-install-go]").addEventListener("click", async () => {
    if (!prompt) return;
    prompt.prompt();
    const choice = await prompt.userChoice.catch(() => ({ outcome: "dismissed" }));
    finish(choice.outcome === "accepted" ? "added" : "dismissed");
  });
})();
