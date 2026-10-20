/*
  Alert me on the watchlist page, sent in place. Without this file the form posts the ordinary way and the
  page comes back saying "Check your inbox". With it the form asks for JSON: "sent" swaps the form for the
  same answer, a busy or failed send shows its message above the form, and anything else (a choice to fix,
  a network failure) sends the form the ordinary way so the page can mark what is wrong.
*/
(function () {
  "use strict";

  if (!window.fetch || !window.FormData) return;

  document.addEventListener("submit", function (event) {
    var form = event.target.closest ? event.target.closest("[data-search-alert]") : null;
    if (!form || form.classList.contains("is-busy")) return;
    event.preventDefault();
    var status = form.querySelector("[data-search-alert-status]");
    form.classList.add("is-busy");
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(function (data) {
        form.classList.remove("is-busy");
        if (data.state !== "sent") {
          status.textContent = data.message;
          status.hidden = false;
          return;
        }
        var done = document.createElement("div");
        done.className = "search-alert__done";
        done.setAttribute("role", "status");
        var title = document.createElement("p");
        title.className = "search-alert__done-title";
        title.textContent = data.title;
        var message = document.createElement("p");
        message.textContent = data.message;
        done.appendChild(title);
        done.appendChild(message);
        var box = form.closest("[data-search-alert-box]");
        var intro = box && box.querySelector(".search-alert__intro");
        if (intro) intro.hidden = true;
        form.replaceWith(done);
      })
      .catch(function () {
        form.submit();
      });
  });
})();
