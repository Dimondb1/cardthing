// The visitor check (web/botcheck.py): find the number that, after the task, makes a SHA-256 hash start with the
// asked-for run of zeros, then send it back. A person's phone does it in about a second, once a week; a crawler on a
// new address every page does it every page. Hashed in batches so the page stays responsive.
(async () => {
  const box = document.querySelector("[data-check]");
  if (!box) return;
  const status = box.querySelector("[data-check-status]");
  // Hidden until now so that with scripts off only the line asking for them shows.
  if (status) status.hidden = false;
  const stop = (message) => {
    if (!status) return;
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
      const params = new URLSearchParams({ task, answer: String(start + found), next: box.dataset.next || "/" });
      window.location.replace(`${box.dataset.url}?${params}`);
      return;
    }
  }
})();
