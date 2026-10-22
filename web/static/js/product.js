// Product page: "Reload prices" fetches the listings we hold now and updates
// the numbers in place. It does not ask the shops; the hourly import and the
// ten-minute stock watcher do that. The icon turns once; prices that changed slide to the
// new value, green if lower, red if higher.

// Compare prices: a plain link to the price list's heading, so it works without this file. Here it glides there
// (or jumps, when the visitor asks for less motion), lands the heading clear of anything stuck to the top of the
// screen, and moves keyboard focus to it so the next Tab goes through the shops.
(() => {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  // Anything fixed or sticky at the top of the screen, measured when it matters: the heading's scroll margin allows for it.
  const stuckHeight = () => {
    let height = 0;
    document.querySelectorAll(".masthead, .stock-tabs").forEach((el) => {
      const position = getComputedStyle(el).position;
      if (position === "fixed" || position === "sticky") height = Math.max(height, el.getBoundingClientRect().bottom);
    });
    return Math.max(0, Math.round(height));
  };
  document.querySelectorAll("a[data-jump]").forEach((link) => {
    link.addEventListener("click", (event) => {
      const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      if (!target) return;
      event.preventDefault();
      document.documentElement.style.setProperty("--sticky-top", `${stuckHeight()}px`);
      target.scrollIntoView({ behavior: reduce.matches ? "auto" : "smooth", block: "start" });
      target.focus({ preventScroll: true });
    });
  });
})();

(() => {
  const button = document.querySelector("[data-refresh]");
  if (!button) return;
  const status = document.querySelector("[data-refresh-status]");
  const icon = button.querySelector(".icon");
  let busy = false;
  let hideTimer = null;

  const say = (text, isError) => {
    status.textContent = text;
    status.classList.toggle("is-error", !!isError);
    status.classList.add("is-shown");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => status.classList.remove("is-shown"), 2600);
  };

  const pence = (text) => Math.round(parseFloat(text.replace(/[^0-9.]/g, "")) * 100) || 0;

  const swap = (el, text) => {
    if (el.textContent.trim() === text) return;
    const direction = pence(text) < pence(el.textContent) ? "money--down" : "money--up";
    el.classList.remove("money--in", "money--down", "money--up");
    el.classList.add("money--out");
    el.addEventListener("animationend", () => {
      const inner = el.querySelector(".price") || el;
      inner.textContent = text;
      el.classList.remove("money--out");
      el.classList.add("money--in", direction);
    }, { once: true });
  };

  button.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    button.setAttribute("aria-busy", "true");
    button.classList.remove("refresh--done");
    try {
      const response = await fetch(button.dataset.refresh, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(response.status);
      const data = await response.json();
      data.listings.forEach((row) => {
        const item = document.querySelector(`[data-listing="${row.id}"]`);
        if (item) {
          swap(item.querySelector("[data-total]"), row.total);
          const checked = item.querySelector("[data-checked]");
          if (checked) checked.textContent = row.checked;
        }
        const main = document.querySelector(`[data-price-for="${row.id}"]`);
        if (main) swap(main, row.price);
        const note = document.querySelector(`[data-note-for="${row.id}"]`);
        if (note) swap(note, row.note);
        const line = document.querySelector(`[data-checked-for="${row.id}"]`);
        if (line) line.textContent = row.checked_line;
      });
      say(button.dataset.done, false);
    } catch (err) {
      say(button.dataset.failed, true);
    }
    button.removeAttribute("aria-busy");
    button.classList.add("refresh--done");
    busy = false;
  });
})();

// Track price and Share: links to the panels under them, which show by the address (#track, #share) without this
// file. Here each opens and closes in place without moving the page, and opening one closes the other. An address
// ending #track keeps that panel shown by :target, so closing is a class of its own.
(() => {
  const pairs = Array.from(document.querySelectorAll("[data-panel-open]"))
    .map((open) => [open, document.getElementById(open.getAttribute("aria-controls"))])
    .filter(([, box]) => box);
  if (!pairs.length) return;
  const shown = (box) => !box.classList.contains("is-closed") && (box.classList.contains("is-open") || location.hash === `#${box.id}`);
  const set = (open, box, on) => {
    box.classList.toggle("is-open", on);
    box.classList.toggle("is-closed", !on);
    open.setAttribute("aria-expanded", String(on));
  };
  pairs.forEach(([open, box]) => open.setAttribute("aria-expanded", String(shown(box))));
  pairs.forEach(([open, box]) => open.addEventListener("click", (event) => {
    event.preventDefault();
    const opening = !shown(box);
    pairs.forEach(([other, otherBox]) => { if (otherBox !== box && shown(otherBox)) set(other, otherBox, false); });
    set(open, box, opening);
    const field = box.querySelector("[data-panel-focus]");
    if (opening && field) field.focus();
  }));
})();

// Price check: a link to the price history, which without this file shows the same facts and chart further down.
// Here it opens the Price check sheet; Close, a tap on the shaded page or Escape closes it.
(() => {
  const sheet = document.querySelector("[data-fair-sheet]");
  const open = document.querySelector("[data-fair-open]");
  if (!sheet || !open || typeof sheet.showModal !== "function") return;
  open.setAttribute("aria-haspopup", "dialog");
  open.addEventListener("click", (event) => {
    event.preventDefault();
    sheet.showModal();
  });
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet || event.target.closest("[data-fair-close]")) sheet.close();
  });
})();
