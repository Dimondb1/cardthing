// Progressive enhancement only. Every page works without this file.

// Apply filters as soon as one changes. The "Show results" button is kept
// for visitors without JavaScript.
document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
  form.querySelectorAll("[data-autosubmit-hide]").forEach((el) => {
    el.hidden = true;
  });
  form.addEventListener("change", () => {
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else {
      form.submit();
    }
  });
});
