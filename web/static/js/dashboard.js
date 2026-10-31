// Progressive enhancement only: the dashboard reads the same without this file.
// Pointing at a day in a chart shows all of that day's figures beside the pointer, values first. Each figure is
// also on the chart, in its caption or in the table under it, so nothing is only here.
(() => {
  const days = document.querySelectorAll(".dash-chart__hit[data-tip]");
  if (!days.length) return;
  const GAP = 12;
  const EDGE = 8;
  const tip = document.createElement("div");
  tip.className = "dash-tip";
  tip.setAttribute("aria-hidden", "true");
  document.body.append(tip);
  let pointed = null;

  const fill = (hit) => {
    // The figures are page text: placed with textContent, never as markup.
    const [day, ...rows] = hit.dataset.tip.split("|");
    const head = document.createElement("span");
    head.className = "dash-tip__day";
    head.textContent = day;
    tip.replaceChildren(head);
    rows.forEach((row) => {
      const cut = row.lastIndexOf(" ");
      const line = document.createElement("span");
      const value = document.createElement("b");
      value.textContent = row.slice(cut + 1);
      line.append(value, ` ${row.slice(0, cut)}`);
      tip.append(line);
    });
  };

  const show = (hit, x, y) => {
    if (pointed !== hit) {
      if (pointed) pointed.classList.remove("is-pointed");
      pointed = hit;
      hit.classList.add("is-pointed");
      fill(hit);
    }
    const box = tip.getBoundingClientRect();
    const left = Math.min(Math.max(EDGE, x - box.width / 2), innerWidth - box.width - EDGE);
    const top = y - box.height - GAP < EDGE ? y + GAP : y - box.height - GAP;
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    tip.classList.add("is-on");
  };

  const hide = () => {
    if (pointed) pointed.classList.remove("is-pointed");
    pointed = null;
    tip.classList.remove("is-on");
  };

  days.forEach((hit) => {
    hit.addEventListener("pointermove", (event) => show(hit, event.clientX, event.clientY));
    hit.addEventListener("pointerdown", (event) => show(hit, event.clientX, event.clientY));
    hit.addEventListener("pointerleave", hide);
  });
  addEventListener("scroll", hide, { passive: true });
})();

// Hiding and arranging the cards (web/dashboard.py dashboard_layout). Without this file each button posts and the
// page comes back to the card; with it, the page changes at once and the change is posted in the background, one
// after another so the last one kept is the last one made.
(() => {
  const grid = document.querySelector("[data-dash-grid]");
  const arrange = document.querySelector("[data-dash-arrange]");
  if (!grid || !arrange || !window.fetch) return;
  const token = arrange.querySelector("[name=csrfmiddlewaretoken]").value;
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  let queue = Promise.resolve();

  const send = (fields) => {
    const body = new FormData();
    body.append("csrfmiddlewaretoken", token);
    Object.entries(fields).forEach(([name, value]) => body.append(name, value));
    queue = queue.then(() => fetch(arrange.action, {
      method: "POST", body, credentials: "same-origin", headers: { "X-Dashboard": "1" },
    })).catch(() => {});
  };

  const cardOf = (key) => grid.querySelector(`[data-card="${CSS.escape(key)}"]`);
  const cards = () => [...grid.querySelectorAll(":scope > [data-card]:not(#arrange)")];
  const rows = () => [...arrange.querySelectorAll("[data-order]")];

  const setHidden = (key, hide) => {
    const card = cardOf(key);
    if (!card) return;
    const button = card.querySelector("[data-fold]");
    card.querySelector(".dash-card__body").hidden = hide;
    card.classList.toggle("is-hidden", hide);
    button.setAttribute("aria-expanded", String(!hide));
    button.value = `${hide ? "show" : "hide"}:${key}`;
    button.querySelector("[data-fold-word]").textContent = hide ? button.dataset.show : button.dataset.hide;
    const tag = arrange.querySelector(`[data-order="${CSS.escape(key)}"] .dash-order__hidden`);
    if (tag) tag.hidden = !hide;
  };

  // As dashboard.placed: a card left alone before a wide one is widened, so the grid has no hole.
  const place = () => {
    const list = cards();
    let column = 0;
    list.forEach((card, index) => {
      card.classList.toggle("dash-card--wide", card.hasAttribute("data-wide"));
      if (card.hasAttribute("data-wide")) {
        if (column === 1) list[index - 1].classList.add("dash-card--wide");
        column = 0;
      } else {
        column = 1 - column;
      }
    });
    rows().forEach((row, index, all) => {
      row.querySelector('[data-move="-1"]').disabled = index === 0;
      row.querySelector('[data-move="1"]').disabled = index === all.length - 1;
    });
  };

  // Continuity: the two rows that swap slide from where they were, on transform alone.
  const slide = (moved) => {
    if (still.matches) return;
    moved.forEach(([row, top]) => {
      const by = top - row.getBoundingClientRect().top;
      if (!by) return;
      row.classList.remove("is-moving");
      row.style.transform = `translateY(${by}px)`;
      row.getBoundingClientRect();
      row.classList.add("is-moving");
      row.style.transform = "";
      row.addEventListener("transitionend", () => row.classList.remove("is-moving"), { once: true });
    });
  };

  document.addEventListener("click", (event) => {
    const fold = event.target.closest("[data-fold]");
    if (fold) {
      event.preventDefault();
      const hide = fold.getAttribute("aria-expanded") === "true";
      setHidden(fold.dataset.fold, hide);
      send({ fold: `${hide ? "hide" : "show"}:${fold.dataset.fold}` });
      return;
    }
    // A link to a hidden card (the figures at the top, Arrange sections) shows it.
    const link = event.target.closest('a[href^="#"]');
    const target = link && link.hash.length > 1 && cardOf(decodeURIComponent(link.hash.slice(1)));
    if (target && target.classList.contains("is-hidden")) {
      setHidden(target.dataset.card, false);
      send({ fold: `show:${target.dataset.card}` });
    }
  });

  arrange.addEventListener("click", (event) => {
    const move = event.target.closest("[data-move]");
    if (move) {
      event.preventDefault();
      const row = move.closest("[data-order]");
      const step = Number(move.dataset.move);
      const other = step < 0 ? row.previousElementSibling : row.nextElementSibling;
      if (!other) return;
      const before = [[row, row.getBoundingClientRect().top], [other, other.getBoundingClientRect().top]];
      // The neighbour moves, not the row pressed, so the pressed button keeps its focus.
      if (step < 0) row.after(other); else row.before(other);
      const card = cardOf(row.dataset.order);
      const otherCard = cardOf(other.dataset.order);
      if (card && otherCard) {
        if (step < 0) card.after(otherCard); else card.before(otherCard);
      }
      place();
      slide(before);
      if (move.disabled) row.querySelector(`[data-move="${-step}"]`).focus();
      send({ order: rows().map((item) => item.dataset.order).join(",") });
      return;
    }
    const every = event.target.closest("[data-every]");
    if (every) {
      event.preventDefault();
      cards().forEach((card) => setHidden(card.dataset.card, every.dataset.every === "hide"));
      send({ every: every.dataset.every });
    }
    // Put back the usual order posts as it is: the page comes back in that order.
  });
})();
