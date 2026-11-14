// Ways to ask Rip besides typing. The owner, 14 Nov 2026: "add a microphone option as it could take a while, and
// upload a shopping list photo". Everything here only speeds things up: without script the box is typed in and the
// photo is a plain file field, and both are sent by the form.
(function () {
  "use strict";

  // Speak instead of typing: the browser's own speech to text writes what is said into the box, after anything
  // already there, to check and send. The button shows only where the browser can do it. Nothing is sent until the
  // visitor presses the form's own button, and RipRaptor only ever receives the words. ask.js swaps in a fresh Ask
  // Rip box after each answer and calls window.ripVoice on it, so its microphone works too.
  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var wire = function (root) {
    if (!Recognition) return;
    Array.prototype.forEach.call(root.querySelectorAll("[data-voice]"), function (button) {
      var field = document.getElementById(button.getAttribute("data-voice"));
      if (!field || button.hasAttribute("data-voice-ready")) return;
      button.setAttribute("data-voice-ready", "");
      var form = button.closest("form");
      var status = form && form.querySelector("[data-voice-status]");
      var label = button.querySelector("[data-voice-label]");
      var idle = label ? label.textContent : "";
      var listening = null;
      var say = function (text) {
        if (!status) return;
        status.textContent = text || "";
        status.hidden = !text;
      };
      button.hidden = false;
      button.addEventListener("click", function () {
        if (listening) { listening.stop(); return; }
        var before = field.value.replace(/\s+$/, "");
        var limit = parseInt(field.getAttribute("maxlength"), 10) || 300;
        var heard = false;
        var failed = false;
        var recognition = new Recognition();
        recognition.lang = document.documentElement.lang || "en-GB";
        recognition.interimResults = true;
        recognition.maxAlternatives = 1;
        recognition.onresult = function (event) {
          var words = "";
          for (var i = 0; i < event.results.length; i += 1) words += event.results[i][0].transcript;
          words = words.trim();
          if (!words) return;
          heard = true;
          field.value = ((before ? before + " " : "") + words).slice(0, limit);
        };
        recognition.onerror = function (event) {
          failed = true;
          if (event.error === "not-allowed" || event.error === "service-not-allowed") say(button.getAttribute("data-voice-denied"));
          else if (event.error !== "aborted") say(button.getAttribute("data-voice-unheard"));
        };
        recognition.onend = function () {
          listening = null;
          button.classList.remove("is-listening");
          if (label) label.textContent = idle;
          if (!heard && !failed) say(button.getAttribute("data-voice-unheard"));
          if (heard) field.dispatchEvent(new Event("input", { bubbles: true }));
        };
        say("");
        try { recognition.start(); } catch (error) { say(button.getAttribute("data-voice-unheard")); return; }
        listening = recognition;
        button.classList.add("is-listening");
        if (label) label.textContent = button.getAttribute("data-voice-stop");
      });
    });
  };
  wire(document);
  window.ripVoice = wire;

  // A photo of the list, on the list builder: the file field becomes a button, a small copy of the chosen photo
  // shows with Remove photo, and the photo is made smaller here (its longest side 1568 pixels, as the server would)
  // so it uploads in a moment on a phone's data. If the browser cannot shrink it, the photo goes as it is.
  var picker = document.querySelector("[data-photo]");
  if (picker) {
    var input = picker.querySelector("input[type=file]");
    var pick = picker.querySelector("label");
    var chosen = picker.querySelector("[data-photo-chosen]");
    var thumb = chosen.querySelector("[data-photo-thumb]");
    var EDGE = 1568;
    var THUMB = 160;
    pick.classList.add("button", "button--quiet");
    input.classList.add("visually-hidden");
    var show = function (preview) {
      chosen.hidden = false;
      pick.hidden = true;
      if (preview) { thumb.src = preview; thumb.hidden = false; }
    };
    var clear = function () {
      input.value = "";
      chosen.hidden = true;
      thumb.hidden = true;
      thumb.removeAttribute("src");
      pick.hidden = false;
    };
    var drawn = function (bitmap, edge) {
      var scale = Math.min(1, edge / Math.max(bitmap.width, bitmap.height));
      var canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      return canvas;
    };
    input.addEventListener("change", function () {
      var file = input.files && input.files[0];
      if (!file) { clear(); return; }
      if (!window.createImageBitmap || !window.DataTransfer) { show(""); return; }
      createImageBitmap(file).then(function (bitmap) {
        var full = drawn(bitmap, EDGE);
        var preview = drawn(bitmap, THUMB).toDataURL("image/jpeg", 0.7);
        if (bitmap.close) bitmap.close();
        full.toBlob(function (blob) {
          if (blob && input.files && input.files[0] === file) {
            var smaller = new DataTransfer();
            smaller.items.add(new File([blob], "list.jpg", { type: "image/jpeg" }));
            input.files = smaller.files;
          }
          show(preview);
        }, "image/jpeg", 0.85);
      }).catch(function () { show(""); });
    });
    chosen.querySelector("[data-photo-remove]").addEventListener("click", function () {
      clear();
      input.focus();   // the file field, hidden from sight but still where the keyboard goes
    });
  }

  // Feedback: reading a photo takes a few seconds, so the button says so and cannot be pressed twice. Coming back to
  // the page puts it back as it was.
  var form = document.querySelector("[data-rip-list]");
  if (form) {
    var send = form.querySelector("[data-working]");
    var words = send ? send.textContent : "";
    form.addEventListener("submit", function () {
      if (!send) return;
      send.textContent = send.getAttribute("data-working");
      send.setAttribute("aria-disabled", "true");
      send.disabled = true;
    });
    window.addEventListener("pageshow", function () {
      if (!send) return;
      send.textContent = words;
      send.removeAttribute("aria-disabled");
      send.disabled = false;
    });
  }
})();
