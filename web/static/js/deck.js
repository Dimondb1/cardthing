// Swipe deck. Pointer events drive a transform on the top card; nothing
// else on the page changes while a finger is down, so it stays at 60fps.
// Right = save (kept in this browser), left = skip, tap = open the product.
(() => {
  const root = document.querySelector("[data-deck]");
  if (!root) return;

  const stack = root.querySelector("[data-deck-stack]");
  // Tell CSS how tall the header really is so the deck can fill the rest.
  const masthead = document.querySelector(".masthead");
  const measure = () => root.style.setProperty("--masthead-height", `${masthead ? masthead.offsetHeight : 0}px`);
  measure();
  addEventListener("resize", measure, { passive: true });
  const hint = root.querySelector("[data-deck-hint]");
  const words = root.dataset;
  const STORE = "cardscout.saved";
  const SEEN = "cardscout.seen";
  const VISIBLE = 3;           // cards rendered in the stack
  const THRESHOLD = 0.32;      // fraction of card width that counts as a swipe
  const FLING = 0.55;          // px per ms

  const game = new URLSearchParams(location.search).get("game") || "";
  let queue = [];
  let nextOffset = 0;
  let loading = false;
  let finished = false;
  let saved = load(STORE, []);
  let seen = new Set(load(SEEN, []));

  function load(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (e) { return fallback; }
  }
  function store(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");

  // Data ------------------------------------------------------------------
  async function fetchMore() {
    if (loading || finished) return;
    loading = true;
    try {
      const url = new URL(root.dataset.deck, location.href);
      url.searchParams.set("offset", nextOffset);
      if (game) url.searchParams.set("game", game);
      const data = await (await fetch(url, { headers: { Accept: "application/json" } })).json();
      data.cards.forEach((c) => { if (!seen.has(c.id)) queue.push(c); });
      data.cards.forEach((c) => { if (c.image) { const img = new Image(); img.src = c.image; } });
      if (data.next === null) finished = true; else nextOffset = data.next;
    } catch (e) {
      finished = true;
    }
    loading = false;
    render();
  }

  // Rendering ---------------------------------------------------------------
  const placeholders = {};
  document.querySelectorAll("template[data-placeholder]").forEach((t) => { placeholders[t.dataset.placeholder] = t.innerHTML; });

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function buildCard(c) {
    const card = el("article", "swipe");
    card.dataset.id = c.id;
    const top = el("div", "swipe__top");
    const image = el("div", "swipe__image");
    if (c.image) { const img = new Image(); img.src = c.image; img.alt = ""; img.draggable = false; img.decoding = "async"; img.addEventListener("load", () => img.classList.add("is-loaded"), { once: true }); image.append(img); }
    else image.innerHTML = placeholders[c.type] || placeholders.other || "";
    const name = el("h2", "swipe__name", c.name);
    const meta = el("p", "swipe__meta", c.meta);
    top.append(image, name, meta);

    const bottom = el("div", "swipe__bottom");
    const offer = el("div", "swipe__offer");
    if (c.badge) {
      const badge = el("span", "badge badge--good", c.badge === "best_week" ? words.bestWeek : words.lowestToday);
      offer.append(badge);
    }
    offer.append(el("span", "price swipe__price", c.price));
    offer.append(el("span", "swipe__at", fill(words.at, { retailer: c.retailer })));
    const buy = el("a", "button button--buy button--card", words.buy);
    buy.href = c.buy; buy.target = "_blank"; buy.rel = "sponsored nofollow noopener";
    buy.dataset.stop = "1";
    bottom.append(offer, buy);
    const rank = el("ol", "rank");
    const first = el("li", "rank__item");
    first.append(el("span", "rank__pos rank__pos--1", "#1"), el("span", "rank__name", c.retailer), el("span", "rank__price", c.price));
    rank.append(first);
    if (c.second) {
      const second = el("li", "rank__item");
      second.append(el("span", "rank__pos rank__pos--2", "#2"), el("span", "rank__name", c.second.retailer), el("span", "rank__price rank__price--more", c.second.price + (c.saving ? " ↑" : "")));
      rank.append(second);
    }
    bottom.append(rank);

    const save = el("span", "swipe__stamp swipe__stamp--save", words.save);
    const skip = el("span", "swipe__stamp swipe__stamp--skip", words.skip);
    card.append(top, bottom, save, skip);
    return card;
  }

  function layout() {
    const cards = Array.from(stack.querySelectorAll(".swipe")).filter((c) => !c.classList.contains("swipe--leaving"));
    cards.forEach((card, i) => {
      const depth = i;
      if (depth === 0 && !card.dataset.shown) {
        card.dataset.shown = "1";
        card.classList.add("swipe--enter");
        card.addEventListener("animationend", () => card.classList.remove("swipe--enter"), { once: true });
      }
      card.style.zIndex = String(10 - depth);
      card.classList.toggle("swipe--behind", depth > 0);
      if (depth > 0) {
        card.style.transform = `translate3d(0, ${depth * 10}px, 0) scale(${1 - depth * 0.04})`;
        card.style.opacity = depth > 1 ? "0" : "1";
      } else {
        card.style.transform = "translate3d(0,0,0)";
        card.style.opacity = "1";
      }
    });
  }

  function render() {
    stack.querySelectorAll(".deck__status").forEach((s) => s.remove());
    const present = stack.querySelectorAll(".swipe:not(.swipe--leaving)").length;
    for (let i = present; i < VISIBLE && i < queue.length; i++) {
      const card = buildCard(queue[i]);
      card.classList.add("swipe--settle");
      stack.append(card);
      attach(card);
    }
    layout();
    if (!queue.length) {
      const status = el("div", "deck__status");
      if (finished) {
        status.append(el("p", "", words.finished));
        const again = el("button", "button button--quiet button--small", words.restartLabel || "Start again");
        again.type = "button";
        again.addEventListener("click", () => { seen = new Set(); store(SEEN, []); nextOffset = 0; finished = false; fetchMore(); });
        status.append(again);
      } else {
        status.append(el("p", "", words.loading));
      }
      stack.append(status);
    }
    if (queue.length < VISIBLE + 2) fetchMore();
  }

  // Gestures ----------------------------------------------------------------
  function attach(card) {
    let startX = 0, startY = 0, dx = 0, dy = 0, dragging = false, moved = false;
    let lastX = 0, lastT = 0, vx = 0;
    const width = () => card.offsetWidth || 320;

    const paint = () => {
      const rot = (dx / width()) * 10;
      card.style.transform = `translate3d(${dx}px, ${dy * 0.35}px, 0) rotate(${rot}deg)`;
      const p = Math.min(Math.abs(dx) / (width() * THRESHOLD), 1);
      card.querySelector(".swipe__stamp--save").style.opacity = dx > 0 ? p : 0;
      card.querySelector(".swipe__stamp--skip").style.opacity = dx < 0 ? p : 0;
      const next = card.nextElementSibling;
      if (next && next.classList.contains("swipe")) {
        next.style.transform = `translate3d(0, ${10 - 10 * p}px, 0) scale(${0.96 + 0.04 * p})`;
      }
    };

    card.addEventListener("pointerdown", (e) => {
      if (e.target.closest("[data-stop]") || e.button !== 0) return;
      if (card !== stack.querySelector(".swipe:not(.swipe--leaving)")) return;
      dragging = true; moved = false;
      startX = lastX = e.clientX; startY = e.clientY; lastT = e.timeStamp; vx = 0;
      card.classList.remove("swipe--settle", "swipe--fly");
      card.setPointerCapture(e.pointerId);
    });
    card.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      dx = e.clientX - startX; dy = e.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      const dt = e.timeStamp - lastT;
      if (dt > 0) { vx = (e.clientX - lastX) / dt; lastX = e.clientX; lastT = e.timeStamp; }
      requestAnimationFrame(paint);
    });
    const release = (e) => {
      if (!dragging) return;
      dragging = false;
      const w = width();
      if (!moved) {
        if (!e.target.closest("[data-stop]")) location.href = new URL(queue[0].url, location.href).href;
        return;
      }
      const goRight = dx > w * THRESHOLD || vx > FLING;
      const goLeft = dx < -w * THRESHOLD || vx < -FLING;
      if (goRight) fly(card, 1);
      else if (goLeft) fly(card, -1);
      else settle(card);
      dx = dy = 0;
    };
    card.addEventListener("pointerup", release);
    card.addEventListener("pointercancel", release);
  }

  function settle(card) {
    card.classList.add("swipe--settle");
    card.style.transform = "translate3d(0,0,0)";
    card.querySelectorAll(".swipe__stamp").forEach((s) => { s.style.opacity = 0; });
    layout();
  }

  function fly(card, dir) {
    if (card.classList.contains("swipe--leaving")) return;
    const item = queue.shift();
    if (!item) return;
    card.classList.add("swipe--leaving", "swipe--fly");
    card.style.zIndex = "20";
    card.querySelector(dir > 0 ? ".swipe__stamp--save" : ".swipe__stamp--skip").style.opacity = 1;
    const x = dir * (window.innerWidth * 0.9 + card.offsetWidth);
    card.style.transform = `translate3d(${x}px, ${-24}px, 0) rotate(${dir * 18}deg)`;
    card.style.opacity = "0";
    card.addEventListener("transitionend", () => card.remove(), { once: true });
    setTimeout(() => card.isConnected && card.remove(), 500);
    seen.add(item.id); store(SEEN, [...seen].slice(-500));
    if (dir > 0 && !saved.some((s) => s.id === item.id)) { saved.push(item); store(STORE, saved); renderSaved(); }
    if (hint) hint.hidden = true;
    render();
  }

  const top = () => stack.querySelector(".swipe:not(.swipe--leaving)");
  root.querySelector("[data-deck-save]").addEventListener("click", () => { const c = top(); if (c) fly(c, 1); });
  root.querySelector("[data-deck-skip]").addEventListener("click", () => { const c = top(); if (c) fly(c, -1); });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    if (e.key === "ArrowRight") { const c = top(); if (c) fly(c, 1); }
    if (e.key === "ArrowLeft") { const c = top(); if (c) fly(c, -1); }
    if (e.key === "Enter") { const c = top(); if (c && queue[0]) location.href = new URL(queue[0].url, location.href).href; }
  });

  // Saved list --------------------------------------------------------------
  const savedList = root.querySelector("[data-saved-list]");
  const savedEmpty = root.querySelector("[data-saved-empty]");
  const savedCount = root.querySelector("[data-saved-count]");
  const savedClear = root.querySelector("[data-saved-clear]");

  function renderSaved() {
    savedList.textContent = "";
    savedEmpty.hidden = saved.length > 0;
    savedClear.hidden = saved.length === 0;
    savedCount.textContent = saved.length ? `(${saved.length})` : "";
    saved.slice().reverse().forEach((c) => {
      const li = el("li", "saving");
      const a = el("a", "saving__link");
      a.href = new URL(c.url, location.href).href;
      const thumb = el("span", "product-image product-image--thumb");
      if (c.image) { const img = new Image(); img.src = c.image; img.alt = ""; thumb.append(img); }
      else thumb.innerHTML = placeholders[c.type] || placeholders.other || "";
      const body = el("span", "saving__body");
      body.append(el("span", "saving__name", c.name));
      const prices = el("span", "saving__prices");
      const best = el("span", "saving__offer");
      best.append(el("span", "saving__retailer", c.retailer), el("span", "saving__best", c.price));
      prices.append(best);
      if (c.second) {
        const second = el("span", "saving__offer saving__offer--second");
        second.append(el("span", "saving__retailer", c.second.retailer), el("span", "saving__second", c.second.price));
        prices.append(second);
      }
      body.append(prices);
      a.append(thumb, body);
      if (c.saving) {
        const pill = el("span", "saving__pill");
        const txt = el("span");
        txt.append(el("strong", "", fill(words.saveAmount, { amount: c.saving })), el("small", "", fill(words.percent, { percent: c.percent })));
        pill.append(txt);
        a.append(pill);
      }
      li.append(a);
      savedList.append(li);
    });
  }
  savedClear.addEventListener("click", () => { saved = []; store(STORE, saved); renderSaved(); });

  root.querySelector("[data-deck-filter] select").addEventListener("change", (e) => e.target.form.submit());

  renderSaved();
  render();
})();
