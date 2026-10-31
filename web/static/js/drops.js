/*
  The drop tracker, made quicker with script. Without it the page already says "Out in 5 days" and every
  reminder form posts to a page of its own.

  - Countdowns: each [data-countdown] holds the drop's start (midnight UK time on its day). Only the digits
    change as it ticks, each in a box two digits wide, so nothing beside them moves. With reduced motion the seconds are
    left out and it changes once a minute. It pauses while the tab is hidden.
  - Reminder forms: Remind me opens the form (a plain details element, so it opens without script too) and
    the email box takes the focus, as it does when another page's Remind me link opens it. The form is sent
    in place, asking for JSON, and the answer replaces it. If the request fails for any reason the form is
    sent the ordinary way instead.
*/
(function () {
  "use strict";

  var clocks = Array.prototype.slice.call(document.querySelectorAll("[data-countdown]"));
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var timer = null;

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function set(el, unit, value) {
    var part = el.querySelector('[data-unit="' + unit + '"]');
    if (part && part.textContent !== value) part.textContent = value;
  }

  function draw() {
    var now = Date.now();
    clocks.forEach(function (el) {
      var left = Math.max(0, Math.floor((el.starts - now) / 1000));
      var days = Math.floor(left / 86400);
      left -= days * 86400;
      var hours = Math.floor(left / 3600);
      left -= hours * 3600;
      var minutes = Math.floor(left / 60);
      set(el, "d", String(days));
      set(el, "h", pad(hours));
      set(el, "m", pad(minutes));
      set(el, "s", pad(left - minutes * 60));
    });
  }

  function schedule() {
    clearTimeout(timer);
    if (document.hidden) return;
    draw();
    var step = calm ? 60000 : 1000;
    timer = setTimeout(schedule, step - (Date.now() % step) + 5);
  }

  clocks = clocks.filter(function (el) {
    el.starts = Date.parse(el.getAttribute("data-countdown"));
    if (isNaN(el.starts)) return false;
    if (calm) {
      var seconds = el.querySelector("[data-seconds]");
      if (seconds) seconds.hidden = true;
    }
    el.hidden = false;
    return true;
  });
  if (clocks.length) {
    schedule();
    document.addEventListener("visibilitychange", schedule);
  }

  function focusEmail(box) {
    var input = box.querySelector("input[type=email]");
    if (input) input.focus({ preventScroll: true });
  }

  document.addEventListener("toggle", function (event) {
    var box = event.target;
    if (!box.matches || !box.matches(".drop-remind") || !box.open) return;
    focusEmail(box);
  }, true);

  // A Remind me link from the home or a game page arrives with that drop's form already open (?remind=),
  // which fires no toggle, so its email box takes the focus here. The browser's jump to #drop- happens at
  // load and clears the focus, so this waits until after it; preventScroll keeps the card where it landed.
  var opened = document.querySelector(".drop-remind[open]");
  if (opened) {
    var focusOpened = function () { setTimeout(function () { focusEmail(opened); }, 0); };
    if (document.readyState === "complete") focusOpened();
    else window.addEventListener("load", focusOpened);
  }

  if (!window.fetch || !window.FormData) return;

  document.addEventListener("submit", function (event) {
    var form = event.target.closest ? event.target.closest("[data-drop-remind]") : null;
    if (!form || form.classList.contains("is-busy")) return;
    event.preventDefault();
    var status = form.querySelector("[data-drop-status]");
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
        status.textContent = data.message;
        status.hidden = false;
        if (data.state === "sent") {
          form.classList.add("is-done");
          // The home page's drop spotlight ticks its alerts step (spotlight.js).
          form.dispatchEvent(new CustomEvent("drop-remind:sent", { bubbles: true }));
        }
      })
      .catch(function () {
        form.submit();
      });
  });
})();
