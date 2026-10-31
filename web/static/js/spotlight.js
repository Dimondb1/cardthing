// The home page's drop spotlight (web/templates/web/includes/spotlight.html). Progressive enhancement only: without
// this file every step works and none is ticked. With it, each step done on this device is ticked and counted, kept
// in this browser alone (the step names, nothing else), so a visitor who comes back sees what is left to do.
(() => {
  const FOLD_AFTER = 1800;   // ms the sign-up's own "check your inbox" shows before the panel folds
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

  // The panel folds to a slim bar with its own Hide and Show (a details element, so it folds without script too).
  // Here the choice is kept for next time, and setting the alerts folds it: the visitor is done with it.
  const panel = document.querySelector("[data-spotlight-panel]");
  const remember = () => {
    try {
      localStorage.setItem(`rr-spotlight:${panel.dataset.set}`, panel.open ? "open" : "closed");
    } catch {
      // Kept for this visit only.
    }
  };
  if (panel) panel.addEventListener("toggle", remember);

  list.addEventListener("drop-remind:sent", () => {
    mark("alerts");
    if (!panel) return;
    panel.querySelector("[data-spotlight-alerted]").hidden = true;
    panel.querySelector("[data-spotlight-confirm]").hidden = false;
    // State: the form's "check your inbox" shows for a moment, then the panel folds to its bar, which says the same,
    // and the bar takes the focus so nobody is left on a form that has gone.
    setTimeout(() => {
      const inside = panel.contains(document.activeElement);
      panel.open = false;
      remember();
      const bar = panel.querySelector("summary");
      bar.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      if (inside) bar.focus({ preventScroll: true });
    }, FOLD_AFTER);
  });
  draw();
})();
