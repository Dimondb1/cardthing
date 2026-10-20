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
