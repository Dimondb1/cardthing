// Inlined at the foot of the home page: the jump buttons for Hunted for you and Your tracks show only while those
// sections do, since both stay hidden until this browser's own picks and history fill them. Without script neither
// section nor its button shows. (Inlined, so it is written without exclamation marks: the house style tests read it.)
(function () {
  var chips = document.querySelectorAll("[data-jump-for]");
  if (chips.length === 0 || typeof MutationObserver === "undefined") return;
  Array.prototype.forEach.call(chips, function (chip) {
    var section = document.querySelector("[" + chip.getAttribute("data-jump-for") + "]");
    if (section === null) return;
    var sync = function () { chip.hidden = section.hidden; };
    sync();
    new MutationObserver(sync).observe(section, { attributes: true, attributeFilter: ["hidden"] });
  });
})();
