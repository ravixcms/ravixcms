/* Nova: colour-scheme toggle and mobile menu. No dependencies. */
(function () {
  "use strict";
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function current() {
    var a = root.getAttribute("data-scheme");
    return a === "dark" || a === "light" ? a : (mq && mq.matches ? "dark" : "light");
  }

  var toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.hidden = false;
    var sync = function () { toggle.setAttribute("aria-pressed", current() === "dark" ? "true" : "false"); };
    sync();
    toggle.addEventListener("click", function () {
      var next = current() === "dark" ? "light" : "dark";
      root.setAttribute("data-scheme", next);
      try { localStorage.setItem("nova-scheme", next); } catch (e) { /* ignore */ }
      sync();
    });
    if (mq && mq.addEventListener) mq.addEventListener("change", sync);
  }

  var btn = document.querySelector(".nav-toggle");
  var panel = document.getElementById("site-nav");
  if (btn && panel) {
    btn.hidden = false;
    root.classList.add("nav-js");
    var set = function (open) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      panel.classList.toggle("is-open", open);
    };
    btn.addEventListener("click", function () { set(btn.getAttribute("aria-expanded") !== "true"); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && btn.getAttribute("aria-expanded") === "true") { set(false); btn.focus(); }
    });
  }
})();
