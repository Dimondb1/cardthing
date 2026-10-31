// The home page's drop spotlight (web/templates/web/includes/spotlight.html). Progressive enhancement only: without
// this file every step works and none is ticked. With it, each step done on this device is ticked and counted, kept
// in this browser alone (the step names, nothing else), so a visitor who comes back sees what is left to do.
(() => {
  const list = document.querySelector("[data-prep]");
  const progress = document.querySelector("[data-prep-progress]");
  if (!list || !progress) return;
  const key = `rr-prep:${list.closest("[data-spotlight]").dataset.spotlight}`;
  const steps = [...list.querySelectorAll("[data-step]")];
  let done = new Set();
  try {
    done = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
  } catch {
    // No storage (a private window, blocked site data): ticks last until the page is left.
  }

  const draw = (fresh) => {
    steps.forEach((step) => {
      const on = done.has(step.dataset.step);
      step.classList.toggle("is-done", on);
      // Feedback: only the step just done animates its tick, not the ones remembered from before.
      step.classList.toggle("is-fresh", on && step.dataset.step === fresh);
      const said = step.querySelector("[data-prep-done]");
      if (said) said.hidden = !on;
    });
    const count = steps.filter((step) => done.has(step.dataset.step)).length;
    progress.textContent = count === steps.length
      ? progress.dataset.ready
      : progress.dataset.progress.replace("{done}", count).replace("{total}", steps.length);
    progress.classList.toggle("is-ready", count === steps.length);
    progress.hidden = false;
  };

  const mark = (name) => {
    if (done.has(name)) return;
    done.add(name);
    try {
      localStorage.setItem(key, JSON.stringify([...done]));
    } catch {
      // Kept for this visit only.
    }
    draw(name);
  };

  list.addEventListener("click", (event) => {
    const link = event.target.closest("[data-prep-link]");
    if (link) mark(link.closest("[data-step]").dataset.step);
  });
  list.addEventListener("drop-remind:sent", () => mark("alerts"));
  draw();
})();
