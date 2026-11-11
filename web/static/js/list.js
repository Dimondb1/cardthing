// Add to list (web/shopping.py): sends the form in place and swaps in the button the server sends back, See your list
// with the new count, so the visitor stays on the product. Without this file the form posts and the page reloads
// with the same button. Anything unexpected falls back to that plain post.
(() => {
  if (!window.fetch || !window.FormData) return;
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-list-form]");
    if (!form || form.dataset.sending) return;
    event.preventDefault();
    form.dataset.sending = "1";
    const holder = form.closest("[data-list-button]");
    fetch(form.action, {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { "X-List": "1", "Accept": "application/json" },
    })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => {
        const wrap = document.createElement("div");
        wrap.innerHTML = data.html || "";
        const fresh = wrap.querySelector("[data-list-button]");
        if (!fresh) throw new Error("no button");
        holder.replaceWith(fresh);
        const link = fresh.querySelector("a");
        if (link) {
          link.classList.add("is-added");
          link.focus();
        }
      })
      .catch(() => {
        delete form.dataset.sending;
        form.submit();
      });
  });
})();
