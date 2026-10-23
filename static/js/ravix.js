/* Ravix — رفتارهای رابط کاربری (بدون وابستگی) */
(function () {
  var gettext = window.gettext || function (s) { return s; };
  var interpolate = window.interpolate || function (s, o) { return s.replace(/%\((\w+)\)s/g, function (m, k) { return o[k]; }); };
  "use strict";

  // ---- منوی کناری در موبایل
  var body = document.body;
  function setSidebar(open) {
    body.classList.toggle("sidebar-open", open);
    var btn = document.querySelector("[data-sidebar-toggle]");
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-sidebar-toggle]")) {
      setSidebar(!body.classList.contains("sidebar-open"));
    } else if (e.target.closest("[data-sidebar-close]")) {
      setSidebar(false);
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setSidebar(false);
  });

  // ---- پیام‌ها (Toast): بستن دستی و بستن خودکار
  function dismiss(toast) {
    toast.classList.add("leaving");
    setTimeout(function () { toast.remove(); }, 260);
  }
  document.querySelectorAll("[data-toast]").forEach(function (toast) {
    var closeBtn = toast.querySelector("[data-toast-close]");
    if (closeBtn) closeBtn.addEventListener("click", function () { dismiss(toast); });
    // پیام‌های خطا دستی بسته شوند؛ بقیه پس از ۷ ثانیه
    if (!toast.classList.contains("toast-error")) setTimeout(function () { dismiss(toast); }, 7000);
  });

  // ---- نمایش/پنهان کردن رمز عبور
  document.querySelectorAll("[data-password-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = btn.parentElement.querySelector("input");
      if (!input) return;
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? gettext("Hide password") : gettext("Show password"));
      btn.setAttribute("aria-pressed", show ? "true" : "false");
    });
  });

  // ---- تأیید پیش از عملیات‌های مخرب (فرم‌های دارای data-confirm)
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) { event.preventDefault(); }
    });
  });

  // ---- جلوگیری از ارسال دوباره فرم (دابل‌کلیک)
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("[type=submit]");
      if (btn) { setTimeout(function () { btn.disabled = true; }, 0); }
    });
  });

  // ---- بارگذاری قالب ZIP: نمایش وضعیت و کنترل ساده حجم (فقط راحتی؛ کنترل واقعی در سرور است)
  document.querySelectorAll("form[data-upload-status]").forEach(function (form) {
    var status = document.getElementById(form.dataset.uploadStatus);
    var input = form.querySelector("input[type=file]");
    form.addEventListener("submit", function (e) {
      var max = input && parseInt(input.dataset.maxBytes, 10);
      if (input && input.files && input.files[0] && max && input.files[0].size > max) {
        e.preventDefault();
        if (status) { status.hidden = false; status.textContent = gettext("The file is larger than the allowed limit."); }
        return;
      }
      if (status) { status.hidden = false; }
    });
  });

  // ---- Text typed by people (titles, summaries, names) follows its own first strong character, so Persian text in an English
  // panel (and English in a Persian one) is aligned and ordered correctly. Technical fields (slug, URL, email, keys …) stay as they are.
  var TECHNICAL_FIELD = /slug|user(name)?$|e-?mail|url|link|phone|tel|key|token|secret|domain|path|host|port|code|color|ip$|regex|css|html|json|handler/i;
  document.querySelectorAll("input[type=text], input:not([type]), input[type=search], textarea").forEach(function (el) {
    if (el.hasAttribute("dir") || el.classList.contains("ltr") || el.readOnly) return;
    if (TECHNICAL_FIELD.test(el.name || "") || TECHNICAL_FIELD.test(el.id || "")) return;
    el.setAttribute("dir", "auto");
  });

  // ---- File inputs: Ravix's own (translated) button and "no file chosen" text, keyboard accessible, with drag and drop.
  // The real <input type=file> stays in the page (transparent, covering the zone), so it is focusable, announced by screen
  // readers and works without any of this; the browser's own English "Choose File / No file chosen" is never shown.
  function acceptsFile(file, accept) {
    if (!accept) return true;
    var name = file.name.toLowerCase(), type = (file.type || "").toLowerCase();
    return accept.split(",").some(function (rule) {
      rule = rule.trim().toLowerCase();
      if (!rule) return false;
      if (rule.charAt(0) === ".") return name.slice(-rule.length) === rule;
      if (rule.slice(-2) === "/*") return type.indexOf(rule.slice(0, -1)) === 0;
      return type === rule;
    });
  }
  document.querySelectorAll("input[type=file]").forEach(function (input) {
    if (input.closest("[data-filedrop]")) return;
    var multiple = input.multiple;
    var zone = document.createElement("div");
    zone.className = "filedrop";
    zone.setAttribute("data-filedrop", "");
    var button = document.createElement("span");
    button.className = "btn btn-light btn-sm filedrop-btn";
    button.setAttribute("aria-hidden", "true");
    button.textContent = multiple ? gettext("Choose files") : gettext("Choose file");
    var text = document.createElement("span");
    text.className = "filedrop-text";
    text.id = (input.id || "file-" + Math.random().toString(36).slice(2)) + "-status";
    text.setAttribute("aria-live", "polite");
    var idle = multiple ? gettext("No files chosen") : gettext("No file chosen");
    input.parentNode.insertBefore(zone, input);
    zone.appendChild(button);
    zone.appendChild(text);
    zone.appendChild(input);
    input.classList.add("filedrop-input");
    input.setAttribute("aria-describedby", ((input.getAttribute("aria-describedby") || "") + " " + text.id).trim());
    if (!input.labels || !input.labels.length) {
      var heading = zone.closest(".field") && zone.closest(".field").querySelector(".field-label");
      input.setAttribute("aria-label", (heading && heading.textContent.trim()) || button.textContent);
    }
    function refresh() {
      var files = input.files ? Array.prototype.slice.call(input.files) : [];
      var label = !files.length ? idle : (files.length === 1 ? files[0].name : interpolate(gettext("Files selected: %(n)s"), {n: files.length}, true));
      text.textContent = label;
      input.title = label;  // otherwise the browser shows its own English tooltip
      zone.classList.toggle("has-file", files.length > 0);
    }
    function setFiles(list) {
      var files = Array.prototype.slice.call(list).filter(function (f) { return acceptsFile(f, input.accept); });
      if (!files.length) { text.textContent = gettext("This file type is not accepted here."); input.title = text.textContent; return; }
      if (!multiple) files = files.slice(0, 1);
      try {
        var dt = new DataTransfer();
        files.forEach(function (f) { dt.items.add(f); });
        input.files = dt.files;
      } catch (e) { return; }
      input.dispatchEvent(new Event("change", {bubbles: true}));
    }
    input.addEventListener("change", refresh);
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (e) {
        if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") < 0) return;
        e.preventDefault();
        zone.classList.add("is-dragover");
        text.textContent = multiple ? gettext("Drop the files here") : gettext("Drop the file here");
      });
    });
    zone.addEventListener("dragleave", function (e) {
      if (e.relatedTarget && zone.contains(e.relatedTarget)) return;
      zone.classList.remove("is-dragover");
      refresh();
    });
    zone.addEventListener("drop", function (e) {
      zone.classList.remove("is-dragover");
      if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) { refresh(); return; }
      e.preventDefault();
      setFiles(e.dataTransfer.files);
    });
    refresh();
  });

  // ---- پیش‌نمایش تصویر انتخاب‌شده
  document.querySelectorAll("input[type=file][data-preview]").forEach(function (input) {
    input.addEventListener("change", function () {
      var target = document.getElementById(input.dataset.preview);
      if (!target || !input.files || !input.files[0]) return;
      var file = input.files[0];
      if (!file.type.startsWith("image/")) return;
      var url = URL.createObjectURL(file);
      target.src = url;
      target.hidden = false;
      target.onload = function () { URL.revokeObjectURL(url); };
    });
  });
})();

/* ---- مرحله دوم: ادیتور Markdown، شمارنده کاراکتر، کپی نشانی ---- */
(function () {
  "use strict";

  function csrfToken() {
    var el = document.querySelector("input[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  document.querySelectorAll("[data-md-editor]").forEach(function (root) {
    var ta = root.querySelector("textarea");
    var preview = root.querySelector("[data-md-preview]");
    var url = root.dataset.previewUrl;
    if (!ta) return;
    ta.setAttribute("dir", "auto"); // Persian text stays right-to-left, English Markdown source left-to-right

    function wrap(before, after, placeholder) {
      var s = ta.selectionStart, e = ta.selectionEnd;
      var sel = ta.value.slice(s, e) || placeholder;
      ta.setRangeText(before + sel + after, s, e, "end");
      ta.focus();
    }
    function linePrefix(prefix) {
      var s = ta.selectionStart;
      var start = ta.value.lastIndexOf("\n", s - 1) + 1;
      ta.setRangeText(prefix, start, start, "end");
      ta.focus();
    }
    var actions = {
      bold: function () { wrap("**", "**", gettext("bold text")); },
      italic: function () { wrap("*", "*", gettext("italic text")); },
      heading: function () { linePrefix("## "); },
      quote: function () { linePrefix("> "); },
      ul: function () { linePrefix("- "); },
      ol: function () { linePrefix("1. "); },
      code: function () { wrap("`", "`", gettext("code")); },
      link: function () { wrap("[", "](https://)", gettext("link text")); },
      image: function () { wrap("![", "](https://)", gettext("image description")); }
    };
    root.querySelectorAll("[data-md]").forEach(function (btn) {
      btn.addEventListener("click", function () { var a = actions[btn.dataset.md]; if (a) a(); });
    });

    root.querySelectorAll("[data-md-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        root.querySelectorAll("[data-md-tab]").forEach(function (t) { t.classList.toggle("active", t === tab); });
        var showPreview = tab.dataset.mdTab === "preview";
        ta.hidden = showPreview;
        preview.hidden = !showPreview;
        if (!showPreview) return;
        preview.textContent = gettext("Loading…");
        var body = new URLSearchParams({ text: ta.value });
        fetch(url, { method: "POST", headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" }, body: body, credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) { preview.innerHTML = d.html || "<p class='muted'>" + gettext("Nothing to preview.") + "</p>"; })
          .catch(function () { preview.textContent = gettext("Preview is not available."); });
      });
    });
  });

  // شمارنده کاراکتر فیلدهای سئو
  document.querySelectorAll("[data-counter]").forEach(function (input) {
    var max = parseInt(input.dataset.counter, 10);
    var note = document.createElement("p");
    note.className = "help";
    input.insertAdjacentElement("afterend", note);
    function update() {
      var n = input.value.length;
      note.textContent = interpolate(gettext("%(n)s of %(max)s characters"), {n: n, max: max}, true);
      note.style.color = n > max ? "var(--c-danger)" : "";
    }
    input.addEventListener("input", update);
    update();
  });

  // کپی نشانی فایل
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    var value = btn.dataset.copy;
    if (value.charAt(0) === "/") value = location.origin + value;
    var done = function () { var old = btn.innerHTML; btn.textContent = gettext("Copied ✓"); setTimeout(function () { btn.innerHTML = old; }, 1500); };
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(value).then(done); }
    else { var t = document.createElement("textarea"); t.value = value; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); done(); }
  });
})();

// ارسال خودکار فرم با تغییر انتخاب (تعویض سایت فعال)
document.addEventListener("change", function (e) {
  var el = e.target;
  if (el && el.matches && el.matches("select[data-autosubmit]") && el.form) { el.form.submit(); }
});

// انتخاب همهٔ ردیف‌ها در جدول‌های عملیات گروهی
document.addEventListener("change", function (e) {
  var el = e.target;
  if (!el || !el.matches || !el.matches("input[data-check-all]")) return;
  var form = el.closest("form");
  if (form) form.querySelectorAll('input[type="checkbox"][name="ids"]').forEach(function (c) { c.checked = el.checked; });
});
