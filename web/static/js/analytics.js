// Google Analytics, only once the visitor allows it. Until then no Google script loads and nothing is
// stored but the answer. The answer is kept in this browser and asked again after six months. Pages
// whose address is a private link (a message thread, a stock alert link) never load this file, and a
// private link the visitor came from is not passed on as the referrer.
(() => {
  const id = (document.querySelector("script[data-ga-id]") || {}).dataset?.gaId;
  const card = document.querySelector("[data-consent]");
  if (!id || !card) return;
  const KEY = "ripraptor.analytics";
  const ASK_AGAIN = 182 * 24 * 60 * 60 * 1000;
  const PRIVATE = /^\/(?:contact\/c|alerts\/(?:confirm|stop))\//;
  if (PRIVATE.test(location.pathname)) return;   // the server leaves this file off these pages; a 404 for one is caught here
  const read = () => {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY));
      return saved && Date.now() - saved.at < ASK_AGAIN ? saved.choice : null;
    } catch (e) { return null; }
  };
  const store = (choice) => { try { localStorage.setItem(KEY, JSON.stringify({ choice, at: Date.now() })); } catch (e) { /* private mode */ } };

  let started = false;
  const start = () => {
    window[`ga-disable-${id}`] = false;
    if (started) {
      window.gtag("consent", "update", { analytics_storage: "granted" });
      return;
    }
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

  // Turning analytics off stops anything more being sent and removes Google's cookies.
  const stop = () => {
    window[`ga-disable-${id}`] = true;   // Google's own switch: the tag sends nothing at all, not even cookieless pings
    if (started) window.gtag("consent", "update", { analytics_storage: "denied" });
    const parts = location.hostname.split(".");
    const domains = ["", ...parts.map((_, i) => `; domain=.${parts.slice(i).join(".")}`)];
    document.cookie.split(";").map((c) => c.split("=")[0].trim()).filter((name) => /^_ga(?:_|$)/.test(name))
      .forEach((name) => domains.forEach((domain) => { document.cookie = `${name}=; Max-Age=0; path=/${domain}`; }));
  };

  // A press on a Buy button or a shop's link, so Analytics can count how often a visit ends at a shop.
  document.addEventListener("click", (e) => {
    const link = e.target.closest && e.target.closest('a[rel~="sponsored"]');
    if (link && started && read() === "granted") window.gtag("event", "buy_click", { link_url: link.href });
  });

  const show = () => { card.hidden = false; requestAnimationFrame(() => card.classList.add("is-shown")); };
  const hide = () => { card.classList.remove("is-shown"); card.hidden = true; };
  card.querySelectorAll("[data-consent-choice]").forEach((button) => button.addEventListener("click", () => {
    const choice = button.dataset.consentChoice;
    store(choice);
    hide();
    if (choice === "granted") start(); else stop();
  }));
  const again = document.querySelector("[data-consent-open]");
  if (again) {
    again.hidden = false;
    again.addEventListener("click", show);
  }

  const choice = read();
  if (choice === "granted") start();
  else if (choice === null) show();
})();
