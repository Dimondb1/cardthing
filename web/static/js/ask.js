// Ask Rip (web/askrip.py) with Rip's face (rip_bot.html, posed by the box's data-mood in site.css). Here Rip looks at the
// box while a question is typed (listening), thinks while the answer is on its way, and stands still while off screen.
// The question is sent without leaving the page and the box the server sends back, with Rip's answer and face, is
// swapped in; focus moves to the answer so a screen reader reads it. A suggested question is a button in a form of its
// own (name "suggested"), sent in place of whatever was typed. Without this file the form posts and the page comes
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

  if (!window.fetch || !window.FormData) return;
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-ask-form]");
    if (!form || form.dataset.sending) return;
    event.preventDefault();
    const box = form.closest(BOX);
    const input = box.querySelector("input[name=question]");
    const chosen = event.submitter && event.submitter.name === "suggested" ? event.submitter : null;
    const question = (chosen ? chosen.value : input ? input.value : "").trim();
    const data = new FormData(form);
    if (chosen) data.set(chosen.name, chosen.value);
    form.dataset.sending = box.dataset.sending = "1";
    box.dataset.mood = "thinking";
    box.querySelectorAll("form[data-ask-form] button").forEach((button) => { button.disabled = true; });
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
        if (!fresh) {
          box.remove();
          return;
        }
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
