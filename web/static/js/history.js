// Recent searches and recently viewed products, kept in this browser only.
// Nothing here is required: the home page simply has no "Pick up where you
// left off" section without it, and the search box shows no recent searches.
(() => {
  const VIEWED = "ripraptor.viewed";
  const SEARCHES = "ripraptor.searches";
  const GAMES = "ripraptor.games";   // game and set pages visited, for interest.js on the home page
  const KEEP_VIEWED = 12;
  const KEEP_SEARCHES = 8;
  const KEEP_GAMES = 8;
  // Only lists are trusted from storage, so odd values never stop the page.
  const load = (key) => {
    try { const value = JSON.parse(localStorage.getItem(key)); return Array.isArray(value) ? value : []; } catch (e) { return []; }
  };
  const store = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ } };
  const items = (key) => load(key).filter((v) => v !== null && typeof v === "object");
  const words = (key) => load(key).filter((v) => typeof v === "string");
  const forget = (key) => { try { localStorage.removeItem(key); } catch (e) { /* private mode */ } };

  // Remember this product page.
  const head = document.querySelector("[data-viewed-slug]");
  if (head) {
    const slug = head.dataset.viewedSlug;
    const viewed = items(VIEWED).filter((v) => v.slug !== slug);
    viewed.unshift({ slug, name: head.dataset.viewedName, game: head.dataset.viewedGame || "" });
    store(VIEWED, viewed.slice(0, KEEP_VIEWED));
  }

  // Remember this game (a game page, or one of its sets).
  const visited = document.querySelector("[data-visited-game]");
  if (visited) {
    const game = visited.dataset.visitedGame;
    store(GAMES, [game, ...words(GAMES).filter((g) => g !== game)].slice(0, KEEP_GAMES));
  }

  // Remember this search.
  const query = document.querySelector("[data-search-query]");
  if (query && query.dataset.searchQuery.trim()) {
    const term = query.dataset.searchQuery.trim();
    const searches = words(SEARCHES).filter((s) => s.toLowerCase() !== term.toLowerCase());
    searches.unshift(term);
    store(SEARCHES, searches.slice(0, KEEP_SEARCHES));
  }

  // Picked for you: products like those viewed, saved and searched for, best deals first. Asked for at
  // once, so the row appears as the page settles. The server keeps nothing about the visitor.
  const picks = document.querySelector("[data-picks]");
  if (picks) {
    const slugOf = (item) => item.slug || (item.url || "").split("/").filter(Boolean).pop() || "";
    const slugs = [...items(VIEWED), ...items("ripraptor.saved")].map(slugOf).filter(Boolean);
    const terms = words(SEARCHES).slice(0, 3);
    if (slugs.length || terms.length) {
      const query = `p=${encodeURIComponent([...new Set(slugs)].join(","))}&s=${encodeURIComponent(terms.join("|"))}`;
      fetch(`${picks.dataset.endpoint}?${query}`)
        .then((r) => (r.ok ? r.text() : ""))
        .then((html) => {
          if (!html.includes("trending__item")) return;
          picks.querySelector("[data-picks-list]").innerHTML = html;   // our own server's markup
          picks.hidden = false;
        })
        .catch(() => {});
    }
  }

  // The home page section.
  const section = document.querySelector("[data-resume]");
  if (!section) return;
  const searches = words(SEARCHES);
  const viewed = items(VIEWED);
  // Game pages visited count too: they reorder the home page, so Clear history must be offered for them.
  if (!searches.length && !viewed.length && !load(GAMES).length) return;

  const searchBlock = section.querySelector("[data-resume-searches]");
  const searchList = section.querySelector("[data-resume-search-list]");
  if (searches.length) {
    searches.forEach((term) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.className = "chip";
      a.href = `${searchList.dataset.searchUrl}?q=${encodeURIComponent(term)}`;
      a.textContent = term;
      li.append(a);
      searchList.append(li);
    });
    searchBlock.hidden = false;
  }

  const viewedBlock = section.querySelector("[data-resume-viewed]");
  const viewedList = section.querySelector("[data-resume-viewed-list]");
  if (viewed.length) {
    const slugs = viewed.map((v) => v.slug).filter(Boolean).join(",");
    fetch(`${viewedBlock.dataset.endpoint}?p=${encodeURIComponent(slugs)}`)
      .then((r) => (r.ok ? r.text() : ""))
      .then((html) => {
        if (!html.includes("trending__item")) return;
        viewedList.innerHTML = html;   // our own server's markup
        viewedBlock.hidden = false;
      })
      .catch(() => {});
  }
  section.hidden = false;

  section.querySelector("[data-resume-clear]").addEventListener("click", () => {
    forget(VIEWED);
    forget(SEARCHES);
    forget(GAMES);
    section.hidden = true;
  });
})();
