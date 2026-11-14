// Ways in besides typing, on every page. The owner, 14 Nov 2026: "add a microphone option as it could take a while, and
// upload a shopping list photo", then "make sure on all search boxes we have voice or image search". Everything here
// only speeds things up: without script the boxes are typed in, the photo is a plain file field, and the forms send.
(function () {
  "use strict";

  // Speak instead of typing: the browser's own speech to text writes what is said into the box, after anything
  // already there. The button shows only where the browser can do it, and RipRaptor only ever receives the words. On
  // Ask Rip's boxes the words wait to be checked and sent, since a question counts; in a search box
  // (data-voice-submit) the search runs as soon as words are heard. A button with its words in aria-label (an icon
  // alone) swaps them for Stop listening while it listens. ask.js swaps in a fresh Ask Rip box after each answer and
  // calls window.ripVoice on it, so its microphone works too.
  //
  // Every step shows (the owner, 14 Nov 2026: "If nothing visible happens, they assume it's broken and try again"):
  // the button turns solid orange and pulses the moment it is tapped, and the box says Listening; the words appear
  // as they are heard; listening stops about a second after the talking does (QUIET_MS), not when the phone gives up;
  // then the box says Searching for what was heard, with a spinner, and the microphone is locked until the page comes.
  var Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  var QUIET_MS = 1000;     // stop this long after the last word heard
  var NOTHING_MS = 8000;   // and give up this long after tapping when nothing at all is heard
  var wire = function (root) {
    if (!Recognition) return;
    Array.prototype.forEach.call(root.querySelectorAll("[data-voice]"), function (button) {
      var field = document.getElementById(button.getAttribute("data-voice"));
      if (!field || button.hasAttribute("data-voice-ready")) return;
      button.setAttribute("data-voice-ready", "");
      var form = button.closest("form");
      var status = form && form.querySelector("[data-voice-status]");
      var label = button.querySelector("[data-voice-label]");
      var idle = label ? label.textContent : button.getAttribute("aria-label");
      var hint = field.getAttribute("placeholder") || "";
      var name = function (words) {
        if (label) label.textContent = words;
        else button.setAttribute("aria-label", words);
      };
      var listening = null;
      var say = function (text) {
        if (!status) return;
        status.textContent = text || "";
        status.hidden = !text;
      };
      var rest = function () {
        button.classList.remove("is-listening");
        if (form) form.classList.remove("is-listening");
        name(idle);
        field.setAttribute("placeholder", hint);
      };
      var searching = function (words) {
        // Locked until the next page: no second search half way through this one.
        rest();
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        if (form) form.classList.add("is-searching");
        var line = (button.getAttribute("data-voice-searching") || "").replace("{words}", words);
        field.value = "";
        field.setAttribute("placeholder", line);
        field.readOnly = true;
        say(line);
      };
      // Coming back to the page (the back button keeps it as it was): ready again.
      window.addEventListener("pageshow", function () {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        if (form) form.classList.remove("is-searching");
        field.readOnly = false;
        rest();
      });
      button.hidden = false;
      button.addEventListener("click", function () {
        if (listening) { listening.stop(); return; }
        var submits = button.hasAttribute("data-voice-submit");
        var before = submits ? "" : field.value.replace(/\s+$/, "");
        var limit = parseInt(field.getAttribute("maxlength"), 10) || 300;
        var heard = false;
        var failed = false;
        var message = "";
        var quiet = null;
        var recognition = new Recognition();
        var stop = function () {
          clearTimeout(quiet);
          if (listening === recognition) recognition.stop();
        };
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
          clearTimeout(quiet);
          quiet = setTimeout(stop, QUIET_MS);
        };
        recognition.onspeechend = stop;
        recognition.onerror = function (event) {
          failed = true;
          if (event.error === "not-allowed" || event.error === "service-not-allowed") message = button.getAttribute("data-voice-denied");
          else if (event.error !== "aborted") message = button.getAttribute("data-voice-unheard");
        };
        recognition.onend = function () {
          clearTimeout(quiet);
          clearTimeout(nothing);
          listening = null;
          rest();
          if (!heard && !failed) message = button.getAttribute("data-voice-unheard");
          if (!heard) {
            // Said where the eye already is: in the box, as well as the line under it.
            say(message);
            if (message && submits) field.setAttribute("placeholder", message);
            return;
          }
          if (!(submits && form)) {
            field.dispatchEvent(new Event("input", { bubbles: true }));
            return;
          }
          var words = field.value.trim();
          searching(words);
          // A search box sends what was heard to Rip (data-voice-ai), who knows the site's sets and so what a
          // misheard word meant; his page falls back to the plain search on its own.
          var ai = button.getAttribute("data-voice-ai");
          if (ai) {
            var query = new URLSearchParams();
            query.set("q", words);
            var game = form.querySelector("input[name=game]");
            if (game && game.value) query.set("game", game.value);
            window.location.href = ai + "?" + query.toString();
          } else {
            field.readOnly = false;
            field.value = words;
            if (form.requestSubmit) form.requestSubmit();
            else form.submit();
          }
        };
        // Shown at once, before the browser has even asked for the microphone.
        say("");
        button.classList.add("is-listening");
        if (form && submits) form.classList.add("is-listening");
        name(button.getAttribute("data-voice-stop"));
        if (submits) field.value = "";
        if (button.getAttribute("data-voice-listening")) field.setAttribute("placeholder", button.getAttribute("data-voice-listening"));
        var nothing = setTimeout(function () { if (!heard) stop(); }, NOTHING_MS);
        try { recognition.start(); } catch (error) {
          clearTimeout(nothing);
          rest();
          say(button.getAttribute("data-voice-unheard"));
          return;
        }
        listening = recognition;
      });
    });
  };
  wire(document);
  window.ripVoice = wire;

  // A photo, on the list builder and on Search by photo: the file field becomes a button, a small copy of the chosen
  // photo shows with Remove photo, and the photo is made smaller here (its longest side 1568 pixels, as the server
  // would) so it uploads in a moment on a phone's data. If the browser cannot shrink it, the photo goes as it is. On
  // Search by photo (data-photo-send) the photo is the whole question, so it is sent as soon as it is ready.
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
      if (picker.hasAttribute("data-photo-send") && input.form) {
        if (input.form.requestSubmit) input.form.requestSubmit();
        else input.form.submit();
      }
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
