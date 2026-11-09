// The visitor check (web/botcheck.py): find the number that, after the task, makes a SHA-256 hash start with the
// asked-for run of zeros, then send it back. A person's phone does it in about a second, once a week; a crawler on a
// new address every page does it every page. Hashed in batches so the page stays responsive.
// With data-tap the answer goes only once the person taps Continue: a crawler that opens pages by itself runs the
// sum (the flood of 9 Nov 2026 did) but never presses a button it has never seen.
(async () => {
  const box = document.querySelector("[data-check]");
  if (!box) return;
  const status = box.querySelector("[data-check-status]");
  const go = box.querySelector("[data-check-go]");
  const tap = Boolean(go);
  const working = () => {
    if (status) status.hidden = false;
  };
  const stop = (message) => {
    if (go) go.hidden = true;
    if (!status) return;
    status.hidden = false;
    status.textContent = message || "";
    status.classList.remove("botcheck__status--working");
  };
  if (!window.crypto || !window.crypto.subtle || !window.TextEncoder) {
    stop(box.dataset.old);
    return;
  }
  // A browser driven by a script says so in ways a person's never does: no sum for it, so no pass (web/botcheck.py).
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  const driven = navigator.webdriver === true
    || /HeadlessChrome|PhantomJS/.test(navigator.userAgent)
    || brands.some((entry) => /headless/i.test(entry.brand))
    || "callPhantom" in window || "_phantom" in window || "__nightmare" in window || "domAutomation" in window
    || Object.keys(document).some((key) => key.indexOf("cdc_") >= 0);
  if (driven) {
    stop(box.dataset.old);
    return;
  }
  // The pass is a cookie: with cookies off the check would only come back, so say so instead of working forever.
  if (!navigator.cookieEnabled) {
    stop(box.dataset.cookies);
    return;
  }

  const task = box.dataset.task;
  const zeros = Number(box.dataset.zeros) || 4;
  let answer = null;
  let wanted = !tap;
  const send = () => {
    const params = new URLSearchParams({ task, answer: String(answer), next: box.dataset.next || "/" });
    window.location.replace(`${box.dataset.url}?${params}`);
  };
  if (tap) {
    go.hidden = false;
    go.addEventListener("click", (event) => {
      // Only a person's own tap or key press: a click made by a script on the page counts for nothing.
      if (!event.isTrusted || wanted) return;
      wanted = true;
      go.setAttribute("aria-disabled", "true");
      if (answer === null) working();
      else send();
    });
  } else {
    working();
  }

  const encoder = new TextEncoder();
  const leads = (buffer) => {
    const bytes = new Uint8Array(buffer);
    for (let k = 0; k < zeros; k += 1) {
      const byte = bytes[k >> 1];
      const nibble = k % 2 === 0 ? byte >> 4 : byte & 15;
      if (nibble !== 0) return false;
    }
    return true;
  };
  const batch = 1500;
  for (let start = 0; start < 50000000; start += batch) {
    const tries = [];
    for (let i = 0; i < batch; i += 1) tries.push(crypto.subtle.digest("SHA-256", encoder.encode(task + (start + i))));
    const hashes = await Promise.all(tries);
    const found = hashes.findIndex(leads);
    if (found >= 0) {
      answer = start + found;
      if (wanted) send();
      return;
    }
  }
})();
