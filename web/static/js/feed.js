// The feed (web/feed.py). The page works without this: every item is a plain link, each row scrolls by itself and the
// search box is a plain form. This adds: the chosen game brought into the bar's view; an arrow at the end of each row
// while there is more to see, which scrolls the row on; and search by voice where the browser can listen.
// No exclamation marks: the copy tests read script as page text.
(function () {
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The game bar never opens scrolled past the chosen game.
  var chosen = document.querySelector(".feed-game[aria-current]");
  var bar = chosen && chosen.parentNode;
  if (bar && bar.scrollWidth > bar.clientWidth) {
    bar.scrollLeft = Math.max(0, chosen.offsetLeft - (bar.clientWidth - chosen.offsetWidth) / 2);
  }

  // The arrow at the end of a row: shown while the row has more to the right, hidden at its end.
  Array.prototype.forEach.call(document.querySelectorAll(".feed__scroller"), function (box) {
    var row = box.querySelector(".feed__row");
    var more = box.querySelector(".feed__more");
    if (!row || !more) return;
    var update = function () { more.hidden = row.scrollWidth - row.clientWidth - row.scrollLeft <= 4; };
    more.addEventListener("click", function () {
      row.scrollBy({ left: Math.max(row.clientWidth * 0.8, 1), behavior: reduced ? "auto" : "smooth" });
    });
    row.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  });

  // Search by voice: the browser listens (Chrome on a phone can, Firefox cannot), the words go in the box and the
  // search runs. Nothing is kept. The button only appears where listening is possible.
  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var mic = document.querySelector(".feed-search__mic");
  var form = mic && mic.closest("form");
  var input = form && form.querySelector("input[type=search]");
  if (!Recognition || !mic || !input) return;
  var label = mic.getAttribute("aria-label");
  var listening = null;
  var stop = function () {
    listening = null;
    form.classList.remove("feed-search--listening");
    mic.setAttribute("aria-label", label);
  };
  mic.hidden = false;
  mic.addEventListener("click", function () {
    if (listening) { listening.abort(); stop(); return; }
    var heard = new Recognition();
    heard.lang = document.documentElement.lang || "en-GB";
    heard.interimResults = false;
    heard.maxAlternatives = 1;
    heard.onresult = function (event) {
      var said = event.results[0] && event.results[0][0] ? event.results[0][0].transcript : "";
      stop();
      if (!said.trim()) return;
      input.value = said.trim();
      form.requestSubmit ? form.requestSubmit() : form.submit();
    };
    heard.onerror = stop;
    heard.onend = stop;
    listening = heard;
    form.classList.add("feed-search--listening");
    mic.setAttribute("aria-label", mic.getAttribute("data-listening") || label);
    try { heard.start(); } catch (e) { stop(); }
  });
})();
