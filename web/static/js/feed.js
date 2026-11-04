// The feed's game bar (web/feed.py): bring the chosen game into view when the page opens, so the bar never opens
// scrolled past it. The bar is plain links and works without this; the page itself is never scrolled.
(function () {
  var chosen = document.querySelector(".feed-game[aria-current]");
  var bar = chosen && chosen.parentNode;
  if (!bar || bar.scrollWidth <= bar.clientWidth) return;
  bar.scrollLeft = Math.max(0, chosen.offsetLeft - (bar.clientWidth - chosen.offsetWidth) / 2);
})();
