// Product page: "Reload prices" fetches the listings we hold now and updates
// the numbers in place. It does not ask the shops; the hourly import and the
// ten-minute stock watcher do that. The icon turns once; prices that changed slide to the
// new value, green if lower, red if higher.
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
