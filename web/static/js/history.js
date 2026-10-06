// Recent searches and recently viewed products, kept in this browser only.
// Nothing here is required: the home page simply has no "Pick up where you
// left off" section without it, and the search box shows no recent searches.
(() => {
  const VIEWED = "ripraptor.viewed";
  const SEARCHES = "ripraptor.searches";
  const KEEP_VIEWED = 12;
  const KEEP_SEARCHES = 8;
  const load = (key) => { try { return JSON.parse(localStorage.getItem(key)) || []; } catch (e) { return []; } };
  const store = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ } };
  const forget = (key) => { try { localStorage.removeItem(key); } catch (e) { /* private mode */ } };

  // Remember this product page.
  const head = document.querySelector("[data-viewed-slug]");
  if (head) {
    const slug = head.dataset.viewedSlug;
    const viewed = load(VIEWED).filter((v) => v.slug !== slug);
    viewed.unshift({ slug, name: head.dataset.viewedName });
    store(VIEWED, viewed.slice(0, KEEP_VIEWED));
  }

  // Remember this search.
  const query = document.querySelector("[data-search-query]");
  if (query && query.dataset.searchQuery.trim()) {
    const term = query.dataset.searchQuery.trim();
    const searches = load(SEARCHES).filter((s) => s.toLowerCase() !== term.toLowerCase());
    searches.unshift(term);
    store(SEARCHES, searches.slice(0, KEEP_SEARCHES));
  }

  // The home page section.
  const section = document.querySelector("[data-resume]");
  if (!section) return;
  const searches = load(SEARCHES);
  const viewed = load(VIEWED);
  if (!searches.length && !viewed.length) return;

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
    section.hidden = true;
  });
})();
