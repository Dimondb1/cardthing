// Ask Rip (web/askrip.py) with Rip's face (rip_bot.html, posed by the box's data-mood in site.css). Here Rip looks at the
// box while a question is typed (listening), thinks while the answer is on its way, and stands still while off screen.
// The question is sent without leaving the page and the box the server sends back, with Rip's answer and face, is
// swapped in; focus moves to the answer so a screen reader reads it. A suggested question is a button in a form of its
// own (name "suggested"), sent in place of whatever was typed. The same questions at the top of the page
// (ask_strip.html) are sent to the box too, after a scroll to it. Without this file the form posts and the page comes
// back with the same answer and face. Anything unexpected falls back to that plain post.
(() => {
  const BOX = "[data-ask]";

  // Rip's sways pause while the box is off screen: no work for nobody.
  const watch = (box) => {
    if (!box || !("IntersectionObserver" in window)) return;
    new IntersectionObserver((entries) => {
      entries.forEach((entry) => entry.target.classList.toggle("is-away", !entry.isIntersecting));
    }).observe(box);
  };
  watch(document.querySelector(BOX));

  const typingIn = (event) => {
    const box = event.target.closest && event.target.closest(BOX);
    return box && event.target.matches("input[name=question]") && !box.dataset.sending ? box : null;
  };
  document.addEventListener("focusin", (event) => {
    const box = typingIn(event);
    if (!box) return;
    if (!box.dataset.rest) box.dataset.rest = box.dataset.mood;
    box.dataset.mood = "listening";
  });
  document.addEventListener("focusout", (event) => {
    const box = typingIn(event);
    if (box && box.dataset.rest) box.dataset.mood = box.dataset.rest;
  });

  // Hide conversation folds the box to its heading and Show conversation; the page stays where the box is. Without
  // this file Hide conversation is a link to the page without the answer.
  document.addEventListener("click", (event) => {
    const hide = event.target.closest && event.target.closest("[data-ask-hide]");
    const show = event.target.closest && event.target.closest("[data-ask-show]");
    const box = (hide || show) && (hide || show).closest(BOX);
    if (!box) return;
    event.preventDefault();
    box.classList.toggle("is-folded", !!hide);
    const again = box.querySelector("[data-ask-show]");
    if (again) again.hidden = !hide;
    if (hide) {
      const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      box.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "nearest" });
      if (again) again.focus({ preventScroll: true });
    } else {
      const answer = box.querySelector("[data-ask-answer]");
      if (answer) answer.focus({ preventScroll: true });
    }
  });

  // Tell me if it drops to £x: Track price opened with that price filled in, where the page has it. Without this
  // file, or where it has not, the link opens the page with the price filled in (?track=).
  document.addEventListener("click", (event) => {
    const offer = event.target.closest && event.target.closest("[data-ask-track]");
    const panel = document.getElementById("track");
    if (!offer || !panel) return;
    event.preventDefault();
    const price = panel.querySelector("input[name=price]");
    if (price) price.value = offer.dataset.askTrack;
    const opener = document.querySelector('[data-panel-open][aria-controls="track"]');
    const open = !panel.classList.contains("is-closed")
      && (panel.classList.contains("is-open") || location.hash === "#track");
    if (opener && !open) opener.click();
    else panel.classList.add("is-open");
    const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    panel.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
    const email = panel.querySelector("input[type=email]");
    if (email) email.focus({ preventScroll: true });
  });

  // Share Rip's answer: the card and the words to the phone's share sheet, or the words alone, or onto the clipboard.
  // Without any of them, or this file, the link opens the card.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest && event.target.closest("[data-ask-share]");
    if (!button) return;
    const text = `${button.dataset.shareText} ${button.dataset.shareUrl}`;
    const canShare = !!navigator.share;
    const canCopy = !!(navigator.clipboard && window.isSecureContext);
    if (!canShare && !canCopy) return;
    event.preventDefault();
    try {
      if (canShare) {
        let files = [];
        try {
          const blob = await (await fetch(button.href, { credentials: "same-origin" })).blob();
          files = [new File([blob], "rip-says.png", { type: "image/png" })];
        } catch (err) {
          files = [];
        }
        if (files.length && navigator.canShare && navigator.canShare({ files })) {
          await navigator.share({ files, title: button.dataset.shareName, text });
        } else {
          await navigator.share({ title: button.dataset.shareName, text: button.dataset.shareText, url: button.dataset.shareUrl });
        }
        return;
      }
      await navigator.clipboard.writeText(text);
      const was = button.textContent;
      button.textContent = button.dataset.shareDone;
      setTimeout(() => { button.textContent = was; }, 4000);
    } catch (err) {
      // Closing the share sheet is not a failure; anything else opens the card instead.
      if (!err || err.name !== "AbortError") window.location.href = button.href;
    }
  });

  if (!window.fetch || !window.FormData) return;
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-ask-form]");
    if (!form || form.dataset.sending) return;
    // A question from the top of the page goes to the box in the price box, which comes into view first.
    const box = form.closest(BOX) || document.querySelector(BOX);
    if (!box) return;
    event.preventDefault();
    if (!form.closest(BOX)) {
      const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      box.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
    }
    const input = box.querySelector("input[name=question]");
    const chosen = event.submitter && event.submitter.name === "suggested" ? event.submitter : null;
    const question = (chosen ? chosen.value : input ? input.value : "").trim();
    const data = new FormData(form);
    if (chosen) data.set(chosen.name, chosen.value);
    form.dataset.sending = box.dataset.sending = "1";
    box.dataset.mood = "thinking";
    document.querySelectorAll("form[data-ask-form] button").forEach((button) => { button.disabled = true; });
    // The question in its own bubble at once, and Rip's bubble saying he is on it.
    let you = box.querySelector("[data-ask-you]");
    if (!you) {
      you = document.createElement("p");
      you.className = "ask__you";
      you.dataset.askYou = "";
      box.querySelector(".ask__chat").before(you);
    }
    you.textContent = question;
    const bubble = box.querySelector("[data-ask-answer]");
    const line = document.createElement("p");
    line.className = "ask__thinking";
    line.textContent = box.dataset.thinking || "";
    bubble.replaceChildren(line);
    const status = box.querySelector("[data-ask-status]");
    if (status) status.textContent = box.dataset.thinking || "";
    fetch(form.action, {
      method: "POST", body: data, credentials: "same-origin",
      headers: { "X-Ask-Rip": "1", "Accept": "application/json" },
    })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => {
        const holder = document.createElement("div");
        holder.innerHTML = data.html || "";
        const fresh = holder.querySelector(BOX);
        // The questions at the top are ready again, or go once Rip takes no more today.
        const more = !!(fresh && fresh.querySelector("form[data-ask-form]"));
        document.querySelectorAll("form[data-ask-strip]").forEach((strip) => {
          delete strip.dataset.sending;
          strip.querySelectorAll("button").forEach((button) => { button.disabled = false; });
          strip.hidden = !more;
        });
        if (!fresh) {
          box.remove();
          return;
        }
        // The price alert offer needs Track price on the page.
        if (!document.getElementById("track")) fresh.querySelectorAll("[data-ask-track]").forEach((offer) => offer.remove());
        box.replaceWith(fresh);
        watch(fresh);
        const answer = fresh.querySelector("[data-ask-answer]");
        if (answer) answer.focus({ preventScroll: true });
      })
      .catch(() => {
        // Let the browser post it the plain way, which always works, carrying the suggested question if one was tapped.
        delete form.dataset.sending;
        if (chosen) {
          const carry = document.createElement("input");
          carry.type = "hidden";
          carry.name = chosen.name;
          carry.value = chosen.value;
          form.append(carry);
        }
        form.submit();
      });
  });
})();
