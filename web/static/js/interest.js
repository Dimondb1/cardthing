// The visitor's own games first in Featured deals and Coming soon on the home page.
// Read from this browser's own history only (recently viewed, saved, recent searches and game pages
// visited, kept by history.js and watch.js). Nothing is sent anywhere and nothing new is kept.
// No exclamation marks: the copy tests read inline script as page text.
// The page includes this file inline straight after those rows, so it runs before they are painted:
// nothing moves on screen and there is nothing to animate. Without it, or without history, the
// server's order (most popular first, the same for everyone) stands.
(() => {
  // Anything stored that is not a list of what we expect is ignored, so odd history never stops the page.
  const read = (key) => {
    try { const value = JSON.parse(localStorage.getItem(key)); return Array.isArray(value) ? value : []; } catch (e) { return []; }
  };
  const isItem = (item) => Boolean(item) && typeof item === "object";
  const fold = (text) => String(text || "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
  let lexicon = [];
  try { lexicon = JSON.parse(document.getElementById("interest-games").textContent) || []; } catch (e) { return; }

  // Each game's name, short name and search aliases, matched as whole words.
  const escape = (word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const games = lexicon.map(([slug, ...words]) => {
    const list = words.map(fold).filter((word) => word.length > 1).map(escape);
    return { slug, re: list.length ? new RegExp(`(^|[^a-z0-9])(${list.join("|")})($|[^a-z0-9])`) : null };
  });
  const known = new Set(games.map((game) => game.slug));
  const gameOf = (...texts) => {
    for (const text of texts) {
      const folded = fold(text).replace(/-/g, " ");
      const hit = folded && games.find((game) => game.re && game.re.test(folded));
      if (hit) return hit.slug;
    }
    return "";
  };
  const slugOf = (item) => item.slug || (item.url || "").split("/").filter(Boolean).pop() || "";

  // Interest by game: newer history counts for more, a saved product or a game page more than a glance.
  const interest = {};
  const add = (slug, weight, i) => { if (known.has(slug)) interest[slug] = (interest[slug] || 0) + weight / (1 + i / 3); };
  read("ripraptor.games").forEach((slug, i) => add(slug, 3, i));
  read("ripraptor.saved").forEach((item, i) => { if (isItem(item)) add(item.game || gameOf(item.name, slugOf(item)), 3, i); });
  read("ripraptor.viewed").forEach((item, i) => { if (isItem(item)) add(item.game || gameOf(item.name, item.slug), 2, i); });
  read("ripraptor.searches").forEach((term, i) => { if (typeof term === "string") add(gameOf(term), 2, i); });
  if (Object.keys(interest).length === 0) return;
  const like = (el) => interest[el.dataset.game] || 0;

  document.querySelectorAll("[data-interest]").forEach((list) => {
    const items = [...list.children];
    const place = new Map(items.map((el, i) => [el, i]));
    const show = parseInt(list.dataset.interestShow, 10) || items.length;
    const spare = (el) => el.hasAttribute("data-spare");
    let chosen = items.filter((el) => spare(el) === false);
    // A spare from one of the visitor's games takes the place of the last shown deal from the same side
    // that is not, so the mix of shops that pay a commission and shops that do not stays as the server set it.
    items.filter((el) => spare(el) && like(el)).forEach((extra) => {
      const out = [...chosen].reverse().find((el) => el.dataset.side === extra.dataset.side && like(el) === 0);
      if (out) chosen[chosen.indexOf(out)] = extra;
    });
    const order = (a, b) => like(b) - like(a) || place.get(a) - place.get(b);
    const shown = chosen.sort(order).slice(0, show);
    const rest = items.filter((el) => shown.includes(el) === false);
    shown.forEach((el) => { el.hidden = false; });
    rest.forEach((el) => { el.hidden = true; });
    list.append(...shown, ...rest);
  });
})();
