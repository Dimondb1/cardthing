// Ask Rip (web/askrip.py): sends the question without leaving the page and swaps in the box the server sends back,
// with the answer in it, then moves focus to the answer so a screen reader reads it. Without this file the form
// posts and the page comes back with the answer. Anything unexpected falls back to that plain post.
(() => {
  if (!window.fetch || !window.FormData) return;
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-ask-form]");
    if (!form || form.dataset.sending) return;
    event.preventDefault();
    form.dataset.sending = "1";
    const box = form.closest("[data-ask]");
    const button = form.querySelector("button");
    const status = box.querySelector("[data-ask-status]");
    if (button) button.disabled = true;
    if (status) status.hidden = false;
    fetch(form.action, {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { "X-Ask-Rip": "1", "Accept": "application/json" },
    })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => {
        const holder = document.createElement("div");
        holder.innerHTML = data.html || "";
        const fresh = holder.querySelector("[data-ask]");
        if (!fresh) {
          box.remove();
          return;
        }
        box.replaceWith(fresh);
        const answer = fresh.querySelector("[data-ask-answer]");
        if (answer) answer.focus({ preventScroll: false });
      })
      .catch(() => {
        // Let the browser post it the plain way, which always works.
        delete form.dataset.sending;
        form.submit();
      });
  });
})();
