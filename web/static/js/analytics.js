// Google Analytics, only once the visitor allows it. Until then no Google script loads and nothing is
// stored but the answer. The answer is kept in this browser and asked again after six months. Pages
// whose address is a private link (a message thread, a stock alert link, the owner's dashboard) never load this file, and a
// private link the visitor came from is not passed on as the referrer.
(() => {
  const id = (document.querySelector("script[data-ga-id]") || {}).dataset?.gaId;
  const card = document.querySelector("[data-consent]");
  if (!id || !card) return;
  const KEY = "ripraptor.analytics";
  const ASK_AGAIN = 182 * 24 * 60 * 60 * 1000;
  const PRIVATE = /^\/(?:contact\/c|alerts\/(?:confirm|stop|manage)|refer\/(?:confirm|claim|address|stop)|r|dashboard)\//;
  if (PRIVATE.test(location.pathname)) return;   // the server leaves this file off these pages; a 404 for one is caught here
  const read = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY));
      return saved && Date.now() - saved.at < ASK_AGAIN ? saved.choice : null;
    } catch (e) { return null; }
  };
  const store = (choice) => { try { localStorage.setItem(KEY, JSON.stringify({ choice, at: Date.now() })); } catch (e) { /* private mode */ } };

  let answer = read();   // this page's answer, so it holds even where the browser keeps no storage
  let started = false;
  const start = () => {
    if (started) return;
    started = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function gtag() { window.dataLayer.push(arguments); };
    window.gtag("consent", "default", {
      analytics_storage: "granted", ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    });
    window.gtag("js", new Date());
    const config = { allow_google_signals: false, allow_ad_personalization_signals: false };
    try {
      const from = new URL(document.referrer);
      if (from.origin === location.origin && PRIVATE.test(from.pathname)) config.page_referrer = `${from.origin}/`;
    } catch (e) { /* no referrer */ }
    window.gtag("config", id, config);
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
    document.head.append(script);
  };

  // Google's cookies (_ga and _ga_<id>), on this host and on each parent domain Google may have used.
  const forget = () => {
    const parts = location.hostname.split(".");
    const domains = ["", ...parts.map((_, i) => `; domain=.${parts.slice(i).join(".")}`)];
    document.cookie.split(";").map((c) => c.split("=")[0].trim()).filter((name) => /^_ga(?:_|$)/.test(name))
      .forEach((name) => domains.forEach((domain) => { document.cookie = `${name}=; Max-Age=0; path=/${domain}`; }));
  };

  // A running tag cannot be fully switched off, so a page that has one reloads without it.
  const apply = () => {
    if (answer === "granted") start();
    if (answer !== "denied") return;
    forget();
    if (started) location.reload();
  };

  // affiliate_click: a press on a link to a shop (every one goes through /go/, so Analytics cannot see it leave as
  // an outbound click), with the button, the shop, the product, its game and the page. compare_prices: a press on
  // a Compare prices link. Our own server saves both too, with or without consent (catalogue/clicks.py).
  document.addEventListener("click", (e) => {
    if (!started || answer !== "granted" || !e.target.closest) return;
    const page_type = document.body.dataset.page || "";
    const link = e.target.closest('a[rel~="sponsored"]');
    if (link) {
      const d = link.dataset;
      window.gtag("event", "affiliate_click", {
        button: d.click || "", retailer: d.clickShop || "", product: d.clickProduct || "", game: d.clickGame || "",
        page_type, link_url: link.href, transport_type: "beacon",
      });
      return;
    }
    const compare = e.target.closest("[data-compare]");
    if (compare) window.gtag("event", "compare_prices", { button: compare.dataset.compare, page_type, transport_type: "beacon" });
  });

  const again = document.querySelector("[data-consent-open]");
  // While the card is up, keyboard focus scrolls to just above it rather than underneath it.
  const show = (focus) => {
    card.hidden = false;
    document.documentElement.style.scrollPaddingBottom = `${innerHeight - card.offsetTop}px`;   // offsetTop ignores the entrance transform
    requestAnimationFrame(() => card.classList.add("is-shown"));
    if (focus) card.querySelector("[data-consent-choice]").focus();
  };
  const hide = () => {
    const hadFocus = card.contains(document.activeElement);
    card.classList.remove("is-shown");
    card.hidden = true;
    document.documentElement.style.scrollPaddingBottom = "";
    if (hadFocus && again) again.focus();
  };
  card.querySelectorAll("[data-consent-choice]").forEach((button) => button.addEventListener("click", () => {
    answer = button.dataset.consentChoice;
    store(answer);
    hide();
    apply();
  }));
  if (again) {
    again.hidden = false;
    again.addEventListener("click", () => show(true));
  }

  // An answer given in another tab, or after this page was kept for the Back button, applies here too.
  const sync = () => {
    const now = read();
    if (now === null || now === answer) return;
    answer = now;
    hide();
    apply();
  };
  addEventListener("storage", (e) => { if (e.key === KEY) sync(); });
  addEventListener("pageshow", (e) => { if (e.persisted) sync(); });

  apply();
  // The swipe page fills a phone's screen and the card would cover Skip and Save: it asks on the next page.
  if (answer === null && !document.body.classList.contains("body--deck")) show(false);
})();
