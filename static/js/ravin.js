/* Ravin CMS — رفتارهای رابط کاربری (بدون وابستگی) */
(function () {
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
      btn.setAttribute("aria-label", show ? "پنهان کردن رمز عبور" : "نمایش رمز عبور");
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
        if (status) { status.hidden = false; status.textContent = "حجم فایل بیشتر از حد مجاز است."; }
        return;
      }
      if (status) { status.hidden = false; }
    });
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
      bold: function () { wrap("**", "**", "متن پررنگ"); },
      italic: function () { wrap("*", "*", "متن کج"); },
      heading: function () { linePrefix("## "); },
      quote: function () { linePrefix("> "); },
      ul: function () { linePrefix("- "); },
      ol: function () { linePrefix("1. "); },
      code: function () { wrap("`", "`", "کد"); },
      link: function () { wrap("[", "](https://)", "متن پیوند"); },
      image: function () { wrap("![", "](https://)", "توضیح تصویر"); }
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
        preview.textContent = "در حال بارگذاری…";
        var body = new URLSearchParams({ text: ta.value });
        fetch(url, { method: "POST", headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/x-www-form-urlencoded" }, body: body, credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) { preview.innerHTML = d.html || "<p class='muted'>چیزی برای نمایش نیست.</p>"; })
          .catch(function () { preview.textContent = "پیش‌نمایش در دسترس نیست."; });
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
      note.textContent = n + " از " + max + " کاراکتر";
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
    var done = function () { var old = btn.innerHTML; btn.textContent = "کپی شد ✓"; setTimeout(function () { btn.innerHTML = old; }, 1500); };
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
