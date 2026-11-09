// The feed (web/feed.py). The page works without this: every item is a plain link, each row scrolls by itself and the
// search box is a plain form. This adds: the chosen game brought into the bar's view; an arrow at the end of each row
// while there is more to see, which scrolls the row on; the hints on each landing; the dock's arrows, the down one
// naming where it goes; and search by voice where the browser can listen. A type
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

  // The hints, on every landing (the owner, 9 Nov 2026: "every time I land on the homepage, I need to see them. They
  // can go ... I don't need to see them after that"): the home page's Pick by game by the bar, then Pick by type by the
  // tiles; a game's page's Pick a set by its row of sets, then Or pick a type by the tiles; and, once a set is picked,
  // Now pick a type by the tiles. Each is on for a moment and gone while the items it points at light up one after
  // another, left to right. Any tap, scroll or key once they are showing ends them early: the visitor is already going.
  var TARGETS = { games: ".feed-game__pill", types: ".feed-tile__well", sets: ".feed-sets .feed-filters__row > .chip" };
  // What a hint sits beside on a wide screen: the last of those items, or for the sets the row's last thing, All sets.
  var BESIDE = { sets: ".feed-sets .feed-filters__row > *" };
  var targetOf = function (hint) {
    for (var name in TARGETS) { if (hint.classList.contains("feed-hint--" + name)) return TARGETS[name]; }
    return "";
  };
  var hintsOver = false;
  var afterHints = [];   // waiting for the hints to be over: the dock's arrow shares their spot
  var whenHintsOver = function (fn) { if (hintsOver) fn(); else afterHints.push(fn); };
  var runHints = function (hints, onEnd) {
    var timers = [];
    var ended = false;
    // On a wide screen the bar, the sets and the tiles sit at the left with room beside them, so each hint goes to
    // the right of the last item, vertically centred on it, with its caret pointing left; on a phone the stylesheet
    // places them.
    if (window.matchMedia && window.matchMedia("(min-width: 48rem)").matches) {
      Array.prototype.forEach.call(hints, function (hint) {
        var within = hint.closest(".feed-dock, .feed-sets, .feed__scroller");
        var beside = hint.classList.contains("feed-hint--sets") ? BESIDE.sets : targetOf(hint);
        var items = within ? within.querySelectorAll(beside) : [];
        var last = items[items.length - 1];
        if (!last) return;
        var box = within.getBoundingClientRect();
        var edge = last.getBoundingClientRect();
        // A row that runs on past the screen's edge keeps its hint under it, where it can be seen.
        if (edge.right + 12 > Math.min(box.right, window.innerWidth)) return;
        hint.classList.add("feed-hint--side");
        hint.style.left = Math.round(edge.right - box.left + 12) + "px";
        hint.style.top = Math.round(edge.top - box.top + edge.height / 2) + "px";
      });
    }
    var show = function (hint) { hint.hidden = false; hint.getBoundingClientRect(); hint.classList.add("is-on"); };
    var hide = function (hint) { hint.classList.remove("is-on"); timers.push(setTimeout(function () { hint.hidden = true; }, reduced ? 0 : 260)); };
    var end = function () {
      if (ended) return;
      ended = true;
      timers.forEach(clearTimeout);
      Array.prototype.forEach.call(document.querySelectorAll(".is-lit"), function (item) { item.classList.remove("is-lit"); });
      Array.prototype.forEach.call(hints, function (hint) { if (!hint.hidden) hide(hint); });
      ["pointerdown", "scroll", "keydown"].forEach(function (name) { window.removeEventListener(name, end, true); });
      if (onEnd) onEnd();
    };
    var at = function (ms, fn) { timers.push(setTimeout(fn, ms)); };
    // The fairy's pass: as a hint shows, the items it points at light up one after another, left to right.
    var pass = function (selector) {
      if (reduced || !selector) return;
      Array.prototype.forEach.call(document.querySelectorAll(selector), function (item, i) {
        at(i * 110, function () { item.classList.add("is-lit"); });
        at(i * 110 + 520, function () { item.classList.remove("is-lit"); });
      });
    };
    Array.prototype.forEach.call(hints, function (hint, i) {
      at(700 + i * 2600, function () { show(hint); pass(targetOf(hint)); });
      at(700 + i * 2600 + 2200, function () { hide(hint); });
    });
    at(700 + hints.length * 2600, end);
    // Listened for from the first hint on, not from the page's load: the browser's own scroll as it settles (the
    // address bar, a restored position) must not end them before they start.
    at(650, function () {
      ["pointerdown", "scroll", "keydown"].forEach(function (name) { window.addEventListener(name, end, true); });
    });
  };
  var landing = document.querySelectorAll(".feed-hint");
  var overNow = function () {
    hintsOver = true;
    afterHints.splice(0).forEach(function (fn) { fn(); });
  };
  if (landing.length) runHints(landing, overNow);
  else overNow();
  // A set picked brings its list in place (browse.js): the tiles' Now pick a type, with their pass.
  document.addEventListener("ripraptor:content", function (event) {
    var fresh = event.detail;
    var next = fresh && fresh.querySelectorAll ? fresh.querySelectorAll(".feed-hint--next") : [];
    if (next.length) runHints(next);
  });
  // A set chip tapped is seen to land at once, before its list comes.
  document.addEventListener("ripraptor:loading", function (event) {
    var link = event.detail && event.detail.source;
    if (link && link.closest && link.closest(".feed-sets") && link.classList.contains("chip")) link.classList.add("is-picking");
  });

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

  // The arrows on the dock's edge, on a phone, where the dock is fixed: down takes the page to the next heading
  // while there is one below, up to the heading before, or the top. The down one began as a hint that there is more
  // under the first screen (the owner, 5 Nov 2026) and still bobs until the page first moves; the owner, 6 Nov 2026:
  // "the little down arrow when pressed should pop the user down to the next heading and then maybe an up one to pop
  // them back up". Both go while the search box has the keyboard up. In its own scope: the hints above share this
  // function's variables, and a second "show" would take theirs.
  (function () {
    var cues = document.querySelector(".feed-dock__cues");
    var dock = cues && cues.closest(".feed-dock");
    if (!cues || !dock || getComputedStyle(dock).position !== "fixed") return;
    var up = cues.querySelector(".feed-dock__cue--up");
    var down = cues.querySelector(".feed-dock__cue--down");
    // The down arrow names where it goes (the owner, 9 Nov 2026): the next heading's words beside the arrow, read
    // out after the arrow's own, and a soft fade as they change.
    var where = down.querySelector(".feed-dock__where");
    var said = down.getAttribute("data-label") || down.getAttribute("aria-label") || "";
    var named = "";
    var name = function (heading) {
      var words = heading ? heading.textContent.replace(/\s+/g, " ").trim() : "";
      if (!where || words === named) return;
      named = words;
      where.textContent = words;
      down.setAttribute("aria-label", words ? said + ": " + words : said);
      where.classList.remove("is-new");
      void where.offsetWidth;
      where.classList.add("is-new");
    };
    var margin = 16;    // a heading lands this far under the top of the screen
    var stops = function () {
      // Every section heading on the page a reader can see, in order; the feed's own title is read out but not shown.
      // Not a product card's name: the arrow goes section to section, not product to product down a list.
      return Array.prototype.filter.call(document.querySelectorAll("main h1, main h2"), function (heading) {
        return heading.offsetHeight > 4 && !heading.closest("[hidden], .card");
      });
    };
    var placeOf = function (heading) { return heading.getBoundingClientRect().top + window.scrollY - margin; };
    var next = function () {
      var here = window.scrollY + margin;
      return stops().filter(function (heading) { return placeOf(heading) > here; })[0] || null;
    };
    var previous = function () {
      var here = window.scrollY - margin;
      var before = stops().filter(function (heading) { return placeOf(heading) < here; });
      return before.length ? before[before.length - 1] : null;
    };
    var quiet = false;   // the search box has the keyboard up
    var showCues = function () {
      var below = next();
      // The page can only move as far as its foot: a heading nearer than that still counts while it is out of sight.
      var limit = document.documentElement.scrollHeight - window.innerHeight;
      down.hidden = quiet || !below || Math.min(placeOf(below), limit) <= window.scrollY + 1;
      if (!down.hidden) name(below);
      up.hidden = quiet || window.scrollY <= margin;
    };
    var settled = function () {
      down.classList.remove("is-new");
      window.removeEventListener("scroll", settled);
    };
    var go = function (heading) {
      var where = heading ? Math.max(0, placeOf(heading)) : 0;
      window.scrollTo({ top: where, behavior: reduced ? "auto" : "smooth" });
    };
    down.addEventListener("click", function () { settled(); go(next()); });
    up.addEventListener("click", function () { go(previous()); });
    dock.addEventListener("focusin", function (event) {
      if (event.target && event.target.matches && event.target.matches("input")) { quiet = true; showCues(); }
    });
    dock.addEventListener("focusout", function (event) {
      if (event.target && event.target.matches && event.target.matches("input")) { quiet = false; showCues(); }
    });
    window.addEventListener("resize", showCues);
    // After the hints, if they are showing: Pick by game sits on the same spot of the dock's edge. A list brought in
    // place (browse.js) has headings of its own.
    whenHintsOver(function () {
      if (window.scrollY <= margin) down.classList.add("is-new");
      showCues();
      window.addEventListener("scroll", showCues, { passive: true });
      window.addEventListener("scroll", settled, { passive: true });
      document.addEventListener("ripraptor:content", function () { setTimeout(showCues, 0); });
    });
  })();

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
