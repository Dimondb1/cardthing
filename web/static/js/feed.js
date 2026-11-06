// The feed (web/feed.py). The page works without this: every item is a plain link, each row scrolls by itself and the
// search box is a plain form. This adds: the chosen game brought into the bar's view; an arrow at the end of each row
// while there is more to see, which scrolls the row on; two hints on the first visits; and search by voice where the
// browser can listen. A type
// tile's list loading in under the tiles is browse.js's work (data-browse-link).
// No exclamation marks: the copy tests read script as page text.
(function () {
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The game bar never opens scrolled past the chosen game.
  var chosen = document.querySelector(".feed-game[aria-current]");
  var bar = chosen && chosen.parentNode;
  if (bar && bar.scrollWidth > bar.clientWidth) {
    bar.scrollLeft = Math.max(0, chosen.offsetLeft - (bar.clientWidth - chosen.offsetWidth) / 2);
  }

  // The arrow at the end of a row: shown while the row has more to the right, hidden at its end. Set up again for
  // the rows browse.js brings in when a type tile's list, or the feed after it, takes the region's place.
  var arrows = function (scope) {
    Array.prototype.forEach.call(scope.querySelectorAll(".feed__scroller"), function (box) {
      var row = box.querySelector(".feed__row");
      var more = box.querySelector(".feed__more");
      if (!row || !more || box.hasAttribute("data-arrow")) return;
      box.setAttribute("data-arrow", "");
      var update = function () { more.hidden = row.scrollWidth - row.clientWidth - row.scrollLeft <= 4; };
      more.addEventListener("click", function () {
        row.scrollBy({ left: Math.max(row.clientWidth * 0.8, 1), behavior: reduced ? "auto" : "smooth" });
      });
      row.addEventListener("scroll", update, { passive: true });
      window.addEventListener("resize", update);
      update();
    });
  };
  arrows(document);
  document.addEventListener("ripraptor:content", function (event) {
    if (event.detail && event.detail.querySelectorAll) arrows(event.detail);
  });

  // A type tile tapped: while browse.js fetches the list, the tile takes its ring, the rails go and a sketch of the
  // list waits under the tiles with the type's name, so the tap is seen to land. The list itself takes the sketch's
  // place when it comes.
  document.addEventListener("ripraptor:loading", function (event) {
    var link = event.detail && event.detail.source;
    var feed = link && link.closest && link.closest(".feed");
    if (!link || !feed || !link.classList.contains("feed-tile__link") || link.hasAttribute("aria-current")) return;
    var tiles = feed.querySelector(".feed__tiles");
    Array.prototype.forEach.call(feed.querySelectorAll(".feed-tile__link[aria-current]"), function (other) {
      other.removeAttribute("aria-current");
    });
    link.setAttribute("aria-current", "page");
    feed.classList.add("feed--filtered");
    Array.prototype.forEach.call(feed.querySelectorAll(".feed__title, .feed__sub, .feed__rail, script"), function (gone) {
      gone.remove();
    });
    var label = (link.querySelector(".feed-tile__label") || link).textContent.trim();
    var sketch = document.createElement("section");
    sketch.className = "feed-sketch";
    sketch.setAttribute("aria-busy", "true");
    var title = document.createElement("h2");
    title.className = "feed-sketch__title";
    title.textContent = label;
    var status = document.createElement("p");
    status.className = "feed-sketch__status";
    status.textContent = (tiles && tiles.getAttribute("data-loading")) || "";
    var grid = document.createElement("div");
    grid.className = "feed-sketch__grid";
    grid.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 4; i += 1) grid.appendChild(document.createElement("span"));
    sketch.appendChild(title);
    sketch.appendChild(status);
    sketch.appendChild(grid);
    feed.appendChild(sketch);
  });

  // The two hints: Pick by game by the bar, then Pick by type by the tiles, each on for a moment and gone. They show on
  // the first landing of each day: a newcomer who did not take them in gets them again, a regular is nudged once a day
  // at most, for a few seconds. (Three landings in all was too few: the owner had landed many more times than that
  // checking the page, and asked where they had gone.) #hints on the address shows them any time. Any tap, scroll or
  // key once they are showing ends them early: the visitor is already going.
  var hints = document.querySelectorAll(".feed-hint");
  var hintsUntil = 0;   // when the hints are over, in ms from now: the dock's arrow waits for them (it shares their spot)
  var HINTS_KEY = "ripraptor.hints";
  var today = new Date().toISOString().slice(0, 10);
  var last = "";
  try { last = localStorage.getItem(HINTS_KEY) || ""; } catch (e) { last = ""; }
  if (hints.length && (last !== today || window.location.hash === "#hints")) {
    try { localStorage.setItem(HINTS_KEY, today); } catch (e) { /* storage off: shown this once */ }
    var timers = [];
    // On a wide screen the bar and the tiles sit at the left with room beside them, so each hint goes to the right of
    // the last item, vertically centred on it, with its caret pointing left; on a phone the stylesheet places them.
    if (window.matchMedia && window.matchMedia("(min-width: 48rem)").matches) {
      var beside = function (hint, selector, within) {
        var items = document.querySelectorAll(selector);
        var last = items[items.length - 1];
        if (!last || !within) return;
        var box = within.getBoundingClientRect();
        var edge = last.getBoundingClientRect();
        hint.classList.add("feed-hint--side");
        hint.style.left = Math.round(edge.right - box.left + 12) + "px";
        hint.style.top = Math.round(edge.top - box.top + edge.height / 2) + "px";
      };
      Array.prototype.forEach.call(hints, function (hint) {
        if (hint.classList.contains("feed-hint--games")) beside(hint, ".feed-game__pill", hint.closest(".feed-dock"));
        else beside(hint, ".feed-tile__well", hint.closest(".feed__scroller"));
      });
    }
    var show = function (hint) { hint.hidden = false; hint.getBoundingClientRect(); hint.classList.add("is-on"); };
    var hide = function (hint) { hint.classList.remove("is-on"); timers.push(setTimeout(function () { hint.hidden = true; }, reduced ? 0 : 260)); };
    var end = function () {
      timers.forEach(clearTimeout);
      Array.prototype.forEach.call(document.querySelectorAll(".is-lit"), function (item) { item.classList.remove("is-lit"); });
      Array.prototype.forEach.call(hints, function (hint) { if (!hint.hidden) hide(hint); });
      ["pointerdown", "scroll", "keydown"].forEach(function (name) { window.removeEventListener(name, end, true); });
    };
    var at = function (ms, fn) { timers.push(setTimeout(fn, ms)); };
    // The fairy's pass: as a hint shows, the items it points at light up one after another, left to right.
    var pass = function (selector) {
      if (reduced) return;
      Array.prototype.forEach.call(document.querySelectorAll(selector), function (item, i) {
        at(i * 110, function () { item.classList.add("is-lit"); });
        at(i * 110 + 520, function () { item.classList.remove("is-lit"); });
      });
    };
    Array.prototype.forEach.call(hints, function (hint, i) {
      at(700 + i * 2600, function () {
        show(hint);
        pass(hint.classList.contains("feed-hint--games") ? ".feed-game__pill" : ".feed-tile__well");
      });
      at(700 + i * 2600 + 2200, function () { hide(hint); });
    });
    hintsUntil = 700 + hints.length * 2600;
    at(hintsUntil, end);
    // Listened for from the first hint on, not from the page's load: the browser's own scroll as it settles (the
    // address bar, a restored position) must not end them before they start.
    at(650, function () {
      ["pointerdown", "scroll", "keydown"].forEach(function (name) { window.addEventListener(name, end, true); });
    });
  }

  // The Set picker's find box on a type's list of a game (views.game_detail): shown only here, where there is script
  // to work it, it narrows the sets as you type. Set up again for a list browse.js brings in.
  var finders = function (scope) {
    Array.prototype.forEach.call(scope.querySelectorAll("[data-set-find]"), function (box) { box.hidden = false; });
  };
  finders(document);
  document.addEventListener("ripraptor:content", function (event) {
    if (event.detail && event.detail.querySelectorAll) finders(event.detail);
  });
  document.addEventListener("input", function (event) {
    var box = event.target;
    if (!box || !box.matches || !box.matches("[data-set-find]")) return;
    var words = box.value.trim().toLowerCase();
    Array.prototype.forEach.call(box.parentNode.querySelectorAll("li"), function (item) {
      item.hidden = Boolean(words) && item.textContent.toLowerCase().indexOf(words) === -1;
    });
  });

  // The down arrow on the dock's edge: shown while the page has more below the first screen and nothing has scrolled
  // yet, on a phone, where the dock is fixed; a tap scrolls on a screen. The first scroll, or a tap in the dock, takes
  // it away. The owner, 5 Nov 2026: "a down arrow to indicate there is content further down".
  var cue = document.querySelector(".feed-dock__cue");
  var dock = cue && cue.closest(".feed-dock");
  if (cue && dock && getComputedStyle(dock).position === "fixed"
      && document.documentElement.scrollHeight > window.innerHeight + 80) {
    var gone = function () {
      cue.classList.add("is-gone");
      window.removeEventListener("scroll", gone);
      dock.removeEventListener("focusin", gone);
    };
    // After the hints, if they are showing: Pick by game sits on the same spot of the dock's edge.
    setTimeout(function () {
      if (window.scrollY > 40) return;
      cue.hidden = false;
      window.addEventListener("scroll", gone, { passive: true });
      dock.addEventListener("focusin", gone);
    }, hintsUntil);
    cue.addEventListener("click", function () {
      window.scrollBy({ top: Math.round(window.innerHeight * 0.7), behavior: reduced ? "auto" : "smooth" });
    });
  }

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
