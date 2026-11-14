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

  // A box that grows with its words (the owner, 14 Nov 2026: "As the words are captured the text box should expand as
  // more words fill it until the message exceeds an acceptable length"). A textarea grows itself, to the height its
  // CSS allows, then scrolls; a one-line box shows the words heard in a panel over itself that grows the same way, to
  // about four lines, keeping the latest words in view (.voice-words).
  var grow = function (field) {
    if (field.tagName !== "TEXTAREA") return;
    field.style.height = "auto";
    if (field.scrollHeight) field.style.height = field.scrollHeight + "px";   // a hidden box measures 0: left to its rows
  };
  var shown = function (field, text, busy) {
    if (field.tagName === "TEXTAREA") { grow(field); return; }
    var holder = field.parentElement;
    var panel = holder.querySelector(":scope > .voice-words");
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "voice-words";
      panel.setAttribute("aria-hidden", "true");
      holder.appendChild(panel);
    }
    panel.textContent = text || "";
    panel.classList.toggle("is-busy", !!busy);
    holder.classList.toggle("is-voicing", !!text);
  };
  Array.prototype.forEach.call(document.querySelectorAll("textarea[data-grow]"), function (field) {
    field.addEventListener("input", function () { grow(field); });
  });
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
      // What the microphone is doing, for whatever holds it (the Ask popup's listening view): listening, searching or
      // rest, with the line the box shows.
      var tell = function (state, line) {
        button.dispatchEvent(new CustomEvent("ripvoice", { bubbles: true, detail: { state: state, line: line || "" } }));
      };
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
        if (!form || !form.classList.contains("is-searching")) shown(field, "");
        tell("rest");
      };
      var searching = function (words) {
        // Locked until the next page: no second search half way through this one.
        rest();
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        if (form) form.classList.add("is-searching");
        var line = (button.getAttribute("data-voice-searching") || "").replace("{words}", words);
        if (field.tagName === "TEXTAREA") {
          field.value = words;   // a growing box keeps the words; the spinner beside it says Rip is on it
          grow(field);
        } else {
          field.value = "";
          field.setAttribute("placeholder", line);
          shown(field, line, true);
        }
        field.readOnly = true;
        say(line);
        tell("searching", line);
      };
      // Coming back to the page (the back button keeps it as it was): ready again.
      window.addEventListener("pageshow", function () {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        if (form) form.classList.remove("is-searching");
        field.readOnly = false;
        rest();
        shown(field, "");
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
          shown(field, field.value);
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
        tell("listening");
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

  // Ask Rip, over the page (the owner, 14 Nov 2026: the header's Ask "should no longer navigate to a page it should
  // act as a toggle ... blurs the current page and then presents the user with the search box"). The Ask button
  // opens and closes it; so do Close, a tap on the blurred page, Escape, and on a phone a swipe down on its top. The
  // header stays sharp above the blur, so Ask can be tapped again. Without script Ask is a link to the Find page.
  var sheet = document.querySelector("[data-ask-sheet]");
  var opener = document.querySelector("[data-ask-open]");
  if (sheet && opener) {
    var panel = sheet.querySelector("[data-ask-panel]");
    var head = document.querySelector(".masthead");
    var title = sheet.querySelector("[data-ask-title]");
    var sub = sheet.querySelector("[data-ask-sub]");
    var listen = sheet.querySelector("[data-ask-listen]");
    var views = sheet.querySelectorAll("[data-ask-view]");
    var productView = sheet.querySelector('[data-ask-view="product"]');
    var allView = sheet.querySelector('[data-ask-view="all"]');
    var sheetForm = sheet.querySelector("[data-ask-sheet-form]");
    var box = sheetForm.querySelector("textarea");
    var firstTitle = title.textContent;
    var show = function (view) {
      Array.prototype.forEach.call(views, function (each) { each.hidden = each !== view; });
      title.textContent = view === allView ? title.getAttribute("data-title-all") : firstTitle;
    };
    // The suggested searches start as the popup opens (the owner, 14 Nov 2026: "a delay happened and I felt unsure
    // what was happening"): Chrome renders them in the background (speculation rules, as base.html does for other
    // links), another browser fetches them, so a tap shows them at once. Once a page, never on Save-Data. They are
    // marked from=ask, and a page fetched ahead counts as a view only once it is shown (site.js), so nothing is
    // counted that nobody opened.
    var ahead = false;
    var startEarly = function () {
      if (ahead) return;
      ahead = true;
      if (navigator.connection && navigator.connection.saveData) return;
      var urls = Array.prototype.map.call(sheet.querySelectorAll("[data-ask-ahead]"), function (link) { return link.href; });
      if (!urls.length) return;
      if (window.HTMLScriptElement && HTMLScriptElement.supports && HTMLScriptElement.supports("speculationrules")) {
        var rules = document.createElement("script");
        rules.type = "speculationrules";
        var own = document.querySelector("script[nonce]");
        if (own) rules.nonce = own.nonce;   // the page's own, which its content security policy asks for
        rules.textContent = JSON.stringify({ prerender: [{ source: "list", urls: urls, eagerness: "immediate" }] });
        document.head.appendChild(rules);
      } else {
        urls.forEach(function (url) {
          var hint = document.createElement("link");
          hint.rel = "prefetch";
          hint.href = url;
          document.head.appendChild(hint);
        });
      }
    };
    var open = function () {
      startEarly();
      var top = head ? Math.max(0, Math.round(head.getBoundingClientRect().bottom)) : 0;
      sheet.style.setProperty("--ask-sheet-top", top + "px");
      show(productView || allView);
      sheet.hidden = false;
      grow(box);
      document.documentElement.classList.add("has-ask-sheet");
      opener.setAttribute("aria-expanded", "true");
      panel.focus({ preventScroll: true });
    };
    var close = function () {
      if (sheet.hidden) return;
      sheet.hidden = true;
      panel.style.transform = "";
      document.documentElement.classList.remove("has-ask-sheet");
      opener.setAttribute("aria-expanded", "false");
      opener.focus({ preventScroll: true });
    };
    opener.addEventListener("click", function (event) {
      event.preventDefault();
      if (sheet.hidden) open();
      else close();
    });
    Array.prototype.forEach.call(sheet.querySelectorAll("[data-ask-close]"), function (closer) {
      closer.addEventListener("click", close);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") close();
    });
    window.addEventListener("pageshow", function () { close(); });

    // Feedback the moment anything here is tapped, until the page comes: the heading says what it is opening
    // (Searching for the words of a suggested search), the tapped thing turns a spinner and a screen reader hears it.
    var goingLine = sheet.querySelector("[data-ask-going]");
    var going = function (link) {
      var named = link.querySelector(".ask-sheet__way-name, .ask-pick__name");
      var what = ((named || link).textContent || link.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim()
        || link.getAttribute("aria-label") || "";
      var line = link.hasAttribute("data-ask-search")
        ? title.getAttribute("data-title-search").replace("{words}", what)
        : title.getAttribute("data-title-opening").replace("{what}", what);
      panel.classList.add("is-going");
      link.classList.add("is-opening");
      link.setAttribute("aria-busy", "true");
      if (link.hasAttribute("data-ask-search")) sheetForm.classList.add("is-searching");
      title.textContent = line;
      goingLine.textContent = line;
    };
    sheet.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[href]");
      if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey
          || event.altKey || link.target === "_blank") return;
      going(link);
    });
    window.addEventListener("pageshow", function () {
      panel.classList.remove("is-going");
      Array.prototype.forEach.call(sheet.querySelectorAll(".is-opening"), function (link) {
        link.classList.remove("is-opening");
        link.removeAttribute("aria-busy");
      });
      goingLine.textContent = "";
    });

    // The arrow that sends shows once there are words.
    Array.prototype.forEach.call(sheet.querySelectorAll("[data-has-words]"), function (field) {
      var mark = function () { field.form.classList.toggle("has-words", !!field.value.trim()); };
      field.addEventListener("input", mark);
      window.addEventListener("pageshow", mark);
    });
    // Enter sends, as in a search box; Shift and Enter starts a new line.
    box.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey) return;
      event.preventDefault();
      if (!box.value.trim()) return;
      if (sheetForm.requestSubmit) sheetForm.requestSubmit();
      else sheetForm.submit();
    });
    // Rip takes a second or two: the box says so, with the spinner in place of the glass, and cannot be sent twice.
    var send = sheetForm.querySelector("[data-working]");
    var sendWords = send.getAttribute("aria-label");
    var status = sheetForm.querySelector("[data-voice-status]");
    sheetForm.addEventListener("submit", function () {
      sheetForm.classList.add("is-searching");
      box.readOnly = true;
      send.setAttribute("aria-label", send.getAttribute("data-working"));
      send.disabled = true;
      if (status) { status.textContent = send.getAttribute("data-working"); status.hidden = false; }
    });
    window.addEventListener("pageshow", function () {
      sheetForm.classList.remove("is-searching");
      box.readOnly = false;
      send.setAttribute("aria-label", sendWords);
      send.disabled = false;
    });

    // The microphone turns the popup into its listening view: Listening, what to say, the words as they come, a big
    // stop button and what is sent; then Searching for what was heard until the results come.
    var mic = sheetForm.querySelector("[data-voice]");
    var stopper = sheet.querySelector("[data-ask-stop]");
    var resting = function () {
      panel.classList.remove("is-listening", "is-voice-searching");
      stopper.disabled = false;
      listen.hidden = true;
      sub.hidden = true;
      title.textContent = title.getAttribute("data-title-all");
    };
    sheetForm.addEventListener("ripvoice", function (event) {
      var state = event.detail.state;
      if (state === "listening") {
        panel.classList.add("is-listening");
        listen.hidden = false;
        sub.hidden = false;
        title.textContent = title.getAttribute("data-title-listening");
      } else if (state === "searching") {
        // The words stay in the box; the stop button becomes the spinner in its place, so nothing jumps.
        panel.classList.remove("is-listening");
        panel.classList.add("is-voice-searching");
        stopper.disabled = true;
        listen.hidden = false;   // the microphone rests a moment before it searches, which put the view away
        sub.hidden = true;
        title.textContent = title.getAttribute("data-title-searching");
      } else if (!panel.classList.contains("is-voice-searching")) {
        resting();
      }
    });
    stopper.addEventListener("click", function () {
      if (mic) mic.click();   // a listening microphone stops when tapped again
    });
    window.addEventListener("pageshow", resting);

    // On a product page: Ask about something else shows the search; a question goes to the Ask Rip box in the price
    // box (ask.js), which answers there, so the popup closes first. A typed one is put in that box and sent from it.
    var other = sheet.querySelector("[data-ask-other]");
    if (other) {
      other.addEventListener("click", function () {
        show(allView);
        box.focus();
      });
    }
    var questions = sheet.querySelector("form[data-ask-strip]");
    if (questions) questions.addEventListener("submit", close);
    var typed = sheet.querySelector("[data-ask-sheet-question]");
    if (typed) {
      typed.addEventListener("submit", function (event) {
        var target = document.getElementById("ask-question");
        if (!target || !target.form) return;   // no box on the page: the form posts the plain way
        event.preventDefault();
        target.value = typed.querySelector("[name=question]").value;
        close();
        if (target.form.requestSubmit) target.form.requestSubmit();
        else target.form.submit();
      });
    }

    // On a phone the popup is a sheet from the foot of the screen: a swipe down on its top closes it. Only its
    // position changes while the finger moves.
    var drag = null;
    var DRAG_CLOSE = 80;
    var sheetLayout = window.matchMedia("(max-width: 47.99rem)");
    Array.prototype.forEach.call(sheet.querySelectorAll("[data-ask-grip]"), function (grip) {
      grip.addEventListener("pointerdown", function (event) {
        if (!sheetLayout.matches || event.button !== 0 || event.target.closest("button, a, input, textarea")) return;
        drag = { y: event.clientY, moved: 0, id: event.pointerId };
        panel.classList.add("is-dragging");
        grip.setPointerCapture(event.pointerId);
      });
      grip.addEventListener("pointermove", function (event) {
        if (!drag || event.pointerId !== drag.id) return;
        drag.moved = Math.max(0, event.clientY - drag.y);
        panel.style.transform = "translateY(" + drag.moved + "px)";
      });
      var end = function (event) {
        if (!drag || event.pointerId !== drag.id) return;
        var moved = drag.moved;
        drag = null;
        panel.classList.remove("is-dragging");
        panel.style.transform = "";
        if (moved > DRAG_CLOSE) close();
      };
      grip.addEventListener("pointerup", end);
      grip.addEventListener("pointercancel", end);
    });
  }

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
