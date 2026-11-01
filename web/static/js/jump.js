// Inlined at the foot of the home page, for the jump row under the search box (inlined, so it is written without
// exclamation marks: the house style tests read it).
//
// The buttons for Hunted for you and Recently viewed show only while those sections do, since both stay hidden until
// this browser's own picks and history fill them. Without script neither section nor its button shows.
//
// The row stays at the top of the screen while the page scrolls (CSS), so it works as the page's tabs: the button of
// the section in view is marked current, in orange, and kept in sight in a row that swipes sideways; once the row is
// stuck it gets a rule under it. Read in one frame per scroll, never during it, and only colours change.
(function () {
  var nav = document.querySelector("[data-jump]");
  if (nav === null) return;

  var chips = nav.querySelectorAll("[data-jump-for]");
  if (typeof MutationObserver === "function") {
    Array.prototype.forEach.call(chips, function (chip) {
      var section = document.querySelector("[" + chip.getAttribute("data-jump-for") + "]");
      if (section === null) return;
      var sync = function () { chip.hidden = section.hidden; };
      sync();
      new MutationObserver(sync).observe(section, { attributes: true, attributeFilter: ["hidden"] });
    });
  }

  nav.classList.add("is-live");
  var list = nav.querySelector("ul");
  var links = Array.prototype.slice.call(nav.querySelectorAll("a[href^='#']"));
  var targets = links.map(function (link) { return document.getElementById(link.getAttribute("href").slice(1)); });
  var current = null;
  var queued = false;

  var mark = function (link) {
    if (link === current) return;
    if (current) current.removeAttribute("aria-current");
    current = link;
    if (link === null) return;
    link.setAttribute("aria-current", "true");
    // Kept in sight when the row swipes sideways on a phone.
    var row = list.getBoundingClientRect();
    var chip = link.getBoundingClientRect();
    if (chip.left < row.left || chip.right > row.right) list.scrollLeft += chip.left - row.left - (row.width - chip.width) / 2;
  };

  var update = function () {
    queued = false;
    var bar = nav.getBoundingClientRect();
    nav.classList.toggle("is-stuck", bar.top <= 0 && window.scrollY > 0);
    // The section in view: the last whose heading has passed a third of the way down the screen.
    var line = Math.max(bar.bottom, window.innerHeight / 3);
    var found = null;
    targets.forEach(function (target, i) {
      if (target === null || links[i].closest("li").hidden) return;
      if (target.getBoundingClientRect().top <= line) found = links[i];
    });
    mark(found);
  };

  var queue = function () {
    if (queued) return;
    queued = true;
    window.requestAnimationFrame(update);
  };
  window.addEventListener("scroll", queue, { passive: true });
  window.addEventListener("resize", queue, { passive: true });
  update();
})();
