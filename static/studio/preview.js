/* Ravix Studio — runs inside the editor's preview frame (same origin as the editor).
   It makes the real theme page selectable and re-orderable, and applies the HTML/CSS the editor sends after every change. */
(function () {
  "use strict";
  var ORIGIN = location.origin;
  var mount = document.getElementById("st-mount");
  var cssEl = document.getElementById("st-css");
  if (!mount || !cssEl) return;
  var selectedId = "", sortables = [], line = null, dropList = null;

  function send(msg) { msg.st = msg.st || "msg"; parent.postMessage(msg, ORIGIN); }
  function nodes(list) { return Array.prototype.filter.call(list.children, function (el) { return el.hasAttribute("data-st-id"); }); }
  function indexIn(list, el) { return nodes(list).indexOf(el); }
  function find(id) { return id ? mount.querySelector('[data-st-id="' + id + '"]') : null; }

  var KIND_LABEL = { section: "section", column: "column" };
  function decorate() {
    Array.prototype.forEach.call(mount.querySelectorAll("[data-st-id]"), function (el) {
      if (el.querySelector(":scope > .st-handle")) return;
      var kind = el.getAttribute("data-st-kind");
      var label = kind === "widget" ? (el.getAttribute("data-st-type") || "widget").replace(/^.*\./, "").replace(/_/g, " ") : (KIND_LABEL[kind] || kind);
      var h = document.createElement("span");
      h.className = "st-handle"; h.setAttribute("aria-hidden", "true"); h.textContent = "⠿ " + label;
      el.insertBefore(h, el.firstChild);
    });
  }

  function setSelected(id) {
    var prev = mount.querySelector(".st-selected");
    if (prev) prev.classList.remove("st-selected");
    selectedId = id || "";
    var el = find(selectedId);
    if (el) el.classList.add("st-selected");
    return el;
  }

  function killSortables() { sortables.forEach(function (s) { s.destroy(); }); sortables = []; }
  function makeSortables() {
    killSortables();
    if (!window.Sortable) return;
    var lists = [mount].concat(Array.prototype.slice.call(mount.querySelectorAll("[data-st-list]")));
    lists.forEach(function (list) {
      var kind = list.getAttribute("data-st-list");
      if (!kind) return;
      sortables.push(window.Sortable.create(list, {
        group: kind, animation: 120, draggable: "[data-st-id]", handle: ".st-handle", filter: ".st-empty", fallbackOnBody: true,
        ghostClass: "sortable-ghost", chosenClass: "sortable-chosen", forceFallback: false, swapThreshold: 0.6,
        onStart: function () { document.body.classList.add("st-dragging"); },
        onEnd: function (evt) {
          document.body.classList.remove("st-dragging");
          if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;
          var to = evt.to;
          send({ st: "move", id: evt.item.getAttribute("data-st-id"), list: kind, owner: to.getAttribute("data-st-owner"), index: indexIn(to, evt.item) });
        }
      }));
    });
  }

  function apply(html, css, selected) {
    var scrollY = window.scrollY;
    cssEl.textContent = css;
    killSortables();
    mount.innerHTML = html;
    decorate();
    makeSortables();
    setSelected(selected !== undefined ? selected : selectedId);
    window.scrollTo(0, scrollY);
    send({ st: "rendered" });
  }

  // ------------------------------------------------------------------ palette drops (HTML5 drag & drop from the editor)
  function hideLine() {
    if (line) line.style.display = "none";
    if (dropList) { dropList.classList.remove("st-drop-ok"); dropList = null; }
  }
  function widgetList(target) { return target && target.closest ? target.closest('[data-st-list="widgets"]') : null; }
  function dropIndex(list, y) {
    var items = nodes(list);
    for (var i = 0; i < items.length; i++) {
      var r = items[i].getBoundingClientRect();
      if (y < r.top + r.height / 2) return { index: i, y: r.top, rect: r };
    }
    var last = items[items.length - 1];
    var lr = last ? last.getBoundingClientRect() : list.getBoundingClientRect();
    return { index: items.length, y: last ? lr.bottom : lr.top + 2, rect: lr };
  }
  function hasPalette(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "text/ravix-studio") >= 0; }

  document.addEventListener("dragover", function (e) {
    if (!hasPalette(e)) return;
    var list = widgetList(e.target);
    if (!list) { hideLine(); return; }
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (dropList !== list) { if (dropList) dropList.classList.remove("st-drop-ok"); dropList = list; list.classList.add("st-drop-ok"); }
    var at = dropIndex(list, e.clientY);
    if (!line) { line = document.createElement("div"); line.className = "st-drop-line"; document.body.appendChild(line); }
    var lr = list.getBoundingClientRect();
    line.style.display = "block"; line.style.top = at.y + "px"; line.style.left = lr.left + "px"; line.style.width = lr.width + "px";
  });
  document.addEventListener("dragleave", function (e) { if (!e.relatedTarget) hideLine(); });
  document.addEventListener("drop", function (e) {
    if (!hasPalette(e)) return;
    var list = widgetList(e.target);
    hideLine();
    if (!list) return;
    e.preventDefault();
    send({ st: "drop", widget: e.dataTransfer.getData("text/ravix-studio"), owner: list.getAttribute("data-st-owner"), index: dropIndex(list, e.clientY).index });
  });

  // ------------------------------------------------------------------ selection, hover, links
  function nearest(target) { return target && target.closest ? target.closest("[data-st-id]") : null; }
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest("a,button,summary,label,input,select,textarea")) e.preventDefault();
    var el = nearest(e.target);
    if (!el) return;
    e.preventDefault();
    setSelected(el.getAttribute("data-st-id"));
    send({ st: "select", id: selectedId });
  }, true);
  document.addEventListener("submit", function (e) { e.preventDefault(); }, true);
  document.addEventListener("mouseover", function (e) {
    var prev = mount.querySelector(".st-hover");
    var el = nearest(e.target);
    if (prev && prev !== el) prev.classList.remove("st-hover");
    if (el) el.classList.add("st-hover");
  });
  document.addEventListener("mouseleave", function () { var p = mount.querySelector(".st-hover"); if (p) p.classList.remove("st-hover"); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { send({ st: "focus-editor" }); return; }
    if (e.ctrlKey || e.metaKey || e.key === "Delete" || (e.altKey && e.key.indexOf("Arrow") === 0)) {
      if (e.key === "Delete" || e.ctrlKey || e.metaKey || e.altKey) e.preventDefault();
      send({ st: "key", key: e.key, ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey, alt: e.altKey });
    }
  });

  // ------------------------------------------------------------------ messages from the editor
  window.addEventListener("message", function (e) {
    if (e.origin !== ORIGIN || e.source !== parent) return;
    var m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.st === "render" && typeof m.html === "string" && typeof m.css === "string") apply(m.html, m.css, m.selected);
    else if (m.st === "select") {
      var el = setSelected(m.id);
      if (el && m.scroll) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });

  decorate();
  makeSortables();
  send({ st: "ready" });
})();
