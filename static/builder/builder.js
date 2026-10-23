/* صفحه‌ساز Ravix — ویرایشگر تصویری (بدون وابستگی، بدون innerHTML برای داده‌ی کاربر).
   منبع حقیقت ساختار و سبک‌ها: جدول SPECS که سرور می‌فرستد (apps/builder/schema.py). */
(function () {
  "use strict";
  var gettext = window.gettext || function (s) { return s; };
  var interpolate = window.interpolate || function (s, o) { return s.replace(/%\((\w+)\)s/g, function (m, k) { return o[k]; }); };
  var IS_FA = (document.documentElement.lang || "").slice(0, 2) === "fa";

  var cfg = JSON.parse(document.getElementById("bx-config").textContent);
  var SPECS = cfg.specs.types, META = cfg.specs.meta, LIM = cfg.specs.limits;
  var TYPE_LABEL = {section: gettext("Section"), row: gettext("Row"), column: gettext("Column"), heading: gettext("Heading"), text: gettext("Text"), image: gettext("Image"), button: gettext("Button")};
  var ROW_PRESETS = [[12], [6, 6], [4, 4, 4], [3, 3, 3, 3], [4, 8], [8, 4]];
  var EQUAL = {1: [12], 2: [6, 6], 3: [4, 4, 4], 4: [3, 3, 3, 3], 5: [3, 3, 2, 2, 2], 6: [2, 2, 2, 2, 2, 2]};
  var $ = function (s, r) { return (r || document).querySelector(s); };

  // ------------------------------------------------------------------ ابزارها
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v == null) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      el.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  function icon(name) {
    var src = $('#bx-icons [data-icon="' + name + '"]');
    var span = h("span", {"aria-hidden": "true"});
    if (src) span.append(src.firstElementChild ? src.firstElementChild.cloneNode(true) : "");
    span.style.display = "inline-flex";
    return span;
  }
  function nid() {
    var a = new Uint8Array(4); crypto.getRandomValues(a);
    return "n" + Array.prototype.map.call(a, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }
  function toast(msg, err) {
    var t = h("div", {"class": "bx-toast" + (err ? " err" : ""), text: msg});
    $("#bx-toasts").append(t);
    setTimeout(function () { t.remove(); }, err ? 6000 : 2800);
  }
  function fa(n) { return IS_FA ? String(n).replace(/\d/g, function (d) { return "\u06F0\u06F1\u06F2\u06F3\u06F4\u06F5\u06F6\u06F7\u06F8\u06F9"[d]; }) : String(n); }
  function isSafeUrl(v) {
    if (!v || v.length > 500 || !/^[^\s\x00-\x1f\x7f\\<>"'`]+$/.test(v)) return false;
    if (v.slice(0, 2) === "//") return false;
    if (v[0] === "/" || v[0] === "#") return true;
    try {
      var u = new URL(v);
      if (u.protocol === "http:" || u.protocol === "https:") return !!u.host && !u.username && !u.password;
      if (u.protocol === "mailto:" || u.protocol === "tel:") return v.length > u.protocol.length;
    } catch (e) { /* نامعتبر */ }
    return false;
  }
  function levelOf(type) { return type === "section" || type === "row" || type === "column" ? type : "element"; }
  function specOf(type, name) { return SPECS[type].filter(function (f) { return f.name === name; })[0]; }
  function defaultsOf(type) { var o = {}; SPECS[type].forEach(function (f) { o[f.name] = f.default; }); return o; }

  // ------------------------------------------------------------------ وضعیت
  var doc = null, revision = 0, live = null, versions = [], canPublish = cfg.canPublish;
  var selected = null, device = "desktop", mode = "edit";
  var undoStack = [], redoStack = [], lastKey = null, lastKeyTime = 0;
  var dirty = false, saving = false, saveTimer = null, blocked = false, resaveAfter = false;
  var mediaCache = {};
  var canvas = $("#bx-canvas"), frame = $("#bx-frame"), preview = $("#bx-preview"), settingsEl = $("#bx-settings"), statusEl = $("#bx-status");

  function walk(fn) {
    doc.sections.forEach(function (s, si) {
      fn(s, "section", doc.sections, si, []);
      s.rows.forEach(function (r, ri) {
        fn(r, "row", s.rows, ri, [s]);
        r.columns.forEach(function (c, ci) {
          fn(c, "column", r.columns, ci, [s, r]);
          c.elements.forEach(function (e, ei) { fn(e, e.type, c.elements, ei, [s, r, c]); });
        });
      });
    });
  }
  function locate(id) {
    var out = null;
    walk(function (n, t, arr, i, anc) { if (n.id === id) out = {node: n, type: t, arr: arr, index: i, anc: anc}; });
    return out;
  }
  function countNodes() { var c = 0; walk(function () { c++; }); return c; }

  // ------------------------------------------------------------------ تاریخچه‌ی واگرد
  function pushHistory(key) {
    var now = Date.now();
    if (key && key === lastKey && now - lastKeyTime < 900) { lastKeyTime = now; return; }
    undoStack.push(JSON.stringify(doc));
    if (undoStack.length > 100) undoStack.shift();
    redoStack = []; lastKey = key || null; lastKeyTime = now;
  }
  function syncHistoryButtons() {
    $("#bx-undo").disabled = !undoStack.length; $("#bx-redo").disabled = !redoStack.length;
  }
  function undo() {
    if (!undoStack.length) return;
    redoStack.push(JSON.stringify(doc)); doc = JSON.parse(undoStack.pop()); lastKey = null; afterChange(true);
  }
  function redo() {
    if (!redoStack.length) return;
    undoStack.push(JSON.stringify(doc)); doc = JSON.parse(redoStack.pop()); lastKey = null; afterChange(true);
  }
  function mutate(fn, opts) {
    opts = opts || {};
    pushHistory(opts.key);
    fn();
    afterChange(opts.settings !== false && !opts.key);
  }
  function afterChange(rebuildSettings) {
    if (selected && !locate(selected)) selected = null;
    dirty = true; setStatus(gettext("Unsaved changes…")); scheduleSave();
    renderCanvas(); syncHistoryButtons();
    if (rebuildSettings) renderSettings();
  }

  // ------------------------------------------------------------------ ذخیره (API)
  function api(url, method, body) {
    var opts = {method: method, credentials: "same-origin", headers: {"X-CSRFToken": cfg.csrf, "Accept": "application/json"}};
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {ok: false, error: gettext("Invalid response from the server.")}; })
        .then(function (data) { return {status: r.status, data: data}; });
    }, function () { return {status: 0, data: {ok: false, error: gettext("Could not connect to the server.")}}; });
  }
  function setStatus(text, cls) { statusEl.textContent = text; statusEl.className = "bx-status" + (cls ? " " + cls : ""); }
  function scheduleSave() { clearTimeout(saveTimer); if (!blocked) saveTimer = setTimeout(save, 1500); }
  function save() {
    clearTimeout(saveTimer);
    if (blocked) return Promise.resolve(false);
    if (saving) { resaveAfter = true; return Promise.resolve(false); }
    if (!dirty) return Promise.resolve(true);
    saving = true; setStatus(gettext("Saving…"));
    var sent = JSON.stringify(doc);
    return api(cfg.urls.draft, "POST", {document: JSON.parse(sent), revision: revision}).then(function (res) {
      saving = false;
      if (res.data.ok) {
        revision = res.data.revision;
        if (JSON.stringify(doc) === sent) dirty = false;
        setStatus(dirty ? gettext("Unsaved changes…") : interpolate(gettext("Saved at %(time)s"), {time: fa(new Date().toLocaleTimeString(IS_FA ? "fa-IR" : undefined, {hour: "2-digit", minute: "2-digit"}))}, true), dirty ? "" : "is-ok");
        if (dirty || resaveAfter) { resaveAfter = false; scheduleSave(); }
        return !dirty;
      }
      if (res.status === 409) {
        blocked = true; setStatus(gettext("Edit conflict — reload the page"), "is-err");
        toast(res.data.error, true);
      } else if (res.status === 422) {
        setStatus(gettext("Data error — not saved"), "is-err"); toast(errorText(res.data), true);
      } else {
        setStatus(gettext("Not saved — we will try again"), "is-err");
        if (res.status !== 401 && res.status !== 403) saveTimer = setTimeout(save, 5000);
        else toast(res.data.error || gettext("You do not have access."), true);
      }
      return false;
    });
  }
  function errorText(data) {
    var list = (data.errors || []).slice(0, 3).map(function (e) { return e.message; });
    return (data.error || gettext("Error")) + (list.length ? " — " + list.join(gettext("; ")) : "");
  }
  window.addEventListener("beforeunload", function (e) { if (dirty && !blocked) { e.preventDefault(); e.returnValue = ""; } });

  // ------------------------------------------------------------------ ساخت گره‌ها
  function makeElement(type) { return {id: nid(), type: type, props: defaultsOf(type)}; }
  function makeRow(spans) {
    return {id: nid(), props: defaultsOf("row"), columns: spans.map(function (s) {
      var p = defaultsOf("column"); p.span = s; return {id: nid(), props: p, elements: []};
    })};
  }
  function makeSection() { return {id: nid(), props: defaultsOf("section"), rows: [makeRow([12])]}; }
  function reId(node) {
    node.id = nid();
    (node.rows || []).forEach(reId); (node.columns || []).forEach(reId); (node.elements || []).forEach(reId);
    return node;
  }

  // ------------------------------------------------------------------ اعمال سبک (مشترک با سرور از طریق SPECS)
  function applyStyle(el, type, props, target) {
    SPECS[type].forEach(function (f) {
      if (!f.css || f.target !== target) return;
      var v = props[f.name];
      if (v == null || v === "") { el.style.removeProperty(f.css); return; }
      if (f.kind === "media") {
        var m = mediaCache[v]; if (!m) { el.style.removeProperty(f.css); return; }
        v = f.template.replace("{}", m.url);
      } else {
        v = v + (f.unit || "");
        if (f.template) v = f.template.replace("{}", v);
      }
      el.style.setProperty(f.css, v);
    });
  }

  // ------------------------------------------------------------------ رندر بوم
  function chrome(type, id) {
    var bar = h("div", {"class": "bx-chrome", "contenteditable": "false"});
    var handle = h("span", {"class": "bx-handle", draggable: "true", title: gettext("Drag to move"), "data-handle": id, "data-kind": levelOf(type), "data-type": type, "aria-label": interpolate(gettext("Move %(type)s"), {type: TYPE_LABEL[type]}, true)}, icon("grip"));
    bar.append(handle, h("span", {"class": "bx-lbl", text: TYPE_LABEL[type]}));
    var horizontal = type === "column";
    [[horizontal ? "right" : "up", "up", horizontal ? gettext("Previous column") : gettext("Up")], [horizontal ? "left" : "down", "down", horizontal ? gettext("Next column") : gettext("Down")], ["copy", "dup", gettext("Duplicate")], ["trash", "del", gettext("Delete")]].forEach(function (b) {
      bar.append(h("button", {type: "button", "data-act": b[1], "data-id": id, title: b[2], "aria-label": b[2] + " " + TYPE_LABEL[type]}, icon(b[0])));
    });
    return bar;
  }
  function needMedia() {
    var need = [];
    walk(function (n, t) {
      if (t === "image" && n.props.media && !mediaCache[n.props.media]) need.push(n.props.media);
      if (t === "section" && n.props.bg_media && !mediaCache[n.props.bg_media]) need.push(n.props.bg_media);
    });
    need = need.filter(function (v, i) { return need.indexOf(v) === i; });
    if (!need.length || needMedia.busy) return;
    needMedia.busy = true;
    api(cfg.urls.media + "?ids=" + need.join(","), "GET").then(function (res) {
      needMedia.busy = false;
      (res.data.items || []).forEach(function (m) { mediaCache[m.id] = m; });
      need.forEach(function (id) { if (!mediaCache[id]) mediaCache[id] = {id: id, url: "", missing: true}; });
      renderCanvas();
    });
  }
  function elementNode(el) {
    var p = el.props, wrap = h("div", {"class": "bx-el bx-" + el.type + " bx-n-" + el.id, "data-id": el.id, "data-type": el.type});
    var cores = [];
    if (el.type === "heading") cores.push(h("h" + p.level, {"class": "bx-core", text: p.text || gettext("(empty heading)")}));
    else if (el.type === "text") {
      var paras = (p.text || "").split("\n\n").filter(function (x) { return x.trim(); });
      if (!paras.length) paras = [gettext("(empty text)")];
      paras.forEach(function (t) {
        var para = h("p", {"class": "bx-core"});
        t.trim().split("\n").forEach(function (line, i) { if (i) para.append(document.createElement("br")); para.append(line); });
        cores.push(para);
      });
    } else if (el.type === "image") {
      var m = p.media ? mediaCache[p.media] : null;
      if (m && m.url) cores.push(h("img", {"class": "bx-core", src: m.url, alt: p.alt || m.alt || "", draggable: "false"}));
      else cores.push(h("div", {"class": "bx-ph bx-ph-img", text: p.media ? (m && m.missing ? gettext("Image not found") : gettext("Loading…")) : gettext("No image selected — select this element and choose an image")}));
    } else cores.push(h("span", {"class": "bx-core bx-btn bx-btn-" + p.variant, text: p.label || gettext("(button)")}));
    cores.forEach(function (c) { if (c.classList.contains("bx-core")) applyStyle(c, el.type, p, "core"); });
    applyStyle(wrap, el.type, p, "outer");
    cores.forEach(function (c) { wrap.append(c); });
    wrap.append(chrome(el.type, el.id));
    return wrap;
  }
  function columnNode(col) {
    var n = h("div", {"class": "bx-col bx-n-" + col.id, "data-id": col.id, "data-type": "column"});
    applyStyle(n, "column", col.props, "outer");
    col.elements.forEach(function (e) { n.append(elementNode(e)); });
    if (!col.elements.length) n.append(h("div", {"class": "bx-ph", text: gettext("Drag an element from the side panel and drop it here")}));
    n.append(chrome("column", col.id));
    return n;
  }
  function rowNode(row) {
    var st = row.props.stack_at;
    var n = h("div", {"class": "bx-row" + (st !== "never" ? " bx-stack-" + st : "") + " bx-n-" + row.id, "data-id": row.id, "data-type": "row"});
    applyStyle(n, "row", row.props, "outer");
    row.columns.forEach(function (c) { n.append(columnNode(c)); });
    n.append(chrome("row", row.id));
    return n;
  }
  function sectionNode(sec) {
    var n = h("section", {"class": "bx-sec" + (sec.props.width === "full" ? " bx-sec-full" : "") + " bx-n-" + sec.id, "data-id": sec.id, "data-type": "section"});
    applyStyle(n, "section", sec.props, "outer");
    var wrap = h("div", {"class": "bx-wrap"});
    sec.rows.forEach(function (r) { wrap.append(rowNode(r)); });
    if (!sec.rows.length) wrap.append(h("div", {"class": "bx-ph", text: gettext("Drag a row from the side panel and drop it here")}));
    n.append(wrap, chrome("section", sec.id));
    return n;
  }
  function renderCanvas() {
    canvas.dataset.device = device;
    canvas.replaceChildren();
    doc.sections.forEach(function (s) { canvas.append(sectionNode(s)); });
    if (!doc.sections.length) canvas.append(h("div", {"class": "bx-canvas-empty"}, h("p", {text: gettext("The page is empty.")}), h("p", {text: gettext("Drag a “Section” from the side panel and drop it here, or click it.")})));
    if (selected) { var s = canvas.querySelector('[data-id="' + selected + '"]'); if (s) s.classList.add("bx-selected"); }
    needMedia();
  }

  // ------------------------------------------------------------------ درج/جابه‌جایی
  function containerArray(level, parentId) {
    if (level === "section") return doc.sections;
    var loc = locate(parentId); if (!loc) return null;
    return level === "row" ? loc.node.rows : loc.node.elements;
  }
  function limitFor(level) { return level === "section" ? LIM.sections : level === "row" ? LIM.rows_per_section : LIM.elements_per_column; }
  function insertAt(level, parentId, index, node, movingId) {
    var arr = containerArray(level, parentId);
    if (!arr) return false;
    var movingWithin = movingId && arr.some(function (n) { return n.id === movingId; });
    if (!movingWithin && arr.length >= limitFor(level)) { toast(interpolate(gettext("You have reached the limit for the number of %(what)s in this section."), {what: level === "element" ? gettext("element") : TYPE_LABEL[level]}, true), true); return false; }
    if (!movingId && countNodes() >= LIM.total_nodes - 10) { toast(gettext("The page has become too large."), true); return false; }
    mutate(function () {
      var idx = index;
      if (movingId) {
        var loc = locate(movingId);
        if (loc.arr === arr && loc.index < idx) idx--;
        loc.arr.splice(loc.index, 1);
      }
      arr.splice(Math.max(0, Math.min(idx, arr.length)), 0, node);
      selected = node.id;
    });
    return true;
  }
  function paletteNode(item) {
    if (item.kind === "section") return makeSection();
    if (item.kind === "row") return makeRow(item.spans);
    return makeElement(item.type);
  }
  /** افزودن با کلیک (جایگزین دسترس‌پذیر Drag & Drop): در محل انتخاب‌شده یا انتهای صفحه. */
  function addByClick(item) {
    var level = item.kind, loc = selected ? locate(selected) : null;
    if (level === "section") {
      var sec = loc ? (loc.type === "section" ? loc.node : loc.anc[0]) : null;
      var idx = sec ? doc.sections.indexOf(sec) + 1 : doc.sections.length;
      return insertAt("section", null, idx, makeSection());
    }
    if (!loc) { toast(level === "row" ? gettext("First select a section.") : gettext("First select a column."), true); return; }
    if (level === "row") {
      var s2 = loc.type === "section" ? loc.node : loc.anc[0];
      var anchorRow = loc.type === "row" ? loc.node : (loc.anc[1] || null);
      var at = anchorRow ? s2.rows.indexOf(anchorRow) + 1 : s2.rows.length;
      return insertAt("row", s2.id, at, paletteNode(item));
    }
    var col = loc.type === "column" ? loc.node : (loc.anc[2] || null);
    if (!col) { toast(gettext("First select a column (or an element inside a column)."), true); return; }
    var atEl = ["heading", "text", "image", "button"].indexOf(loc.type) >= 0 ? col.elements.indexOf(loc.node) + 1 : col.elements.length;
    insertAt("element", col.id, atEl, paletteNode(item));
  }

  // ------------------------------------------------------------------ Drag & Drop (HTML5)
  var drag = null;
  function clearMarks() {
    canvas.querySelectorAll(".bx-dropline,.bx-dropline-after,.bx-dropin").forEach(function (n) { n.classList.remove("bx-dropline", "bx-dropline-after", "bx-dropin"); });
  }
  /** هدف رها کردن را از موقعیت اشاره‌گر و نوع آیتم در حال کشیدن محاسبه می‌کند. */
  function resolveTarget(e) {
    if (!drag) return null;
    var el = e.target.nodeType === 1 ? e.target : e.target.parentElement;
    if (!el || !canvas.contains(el) && el !== canvas) return null;
    var kind = drag.kind;
    function side(node) { var r = node.getBoundingClientRect(); return e.clientY > r.top + r.height / 2; }
    if (kind === "section") {
      var s = el.closest(".bx-sec");
      if (!s) return {level: "section", parentId: null, index: doc.sections.length, mark: null, markClass: ""};
      var after = side(s), i = doc.sections.findIndex(function (x) { return x.id === s.dataset.id; });
      return {level: "section", parentId: null, index: i + (after ? 1 : 0), mark: s, markClass: after ? "bx-dropline-after" : "bx-dropline"};
    }
    if (kind === "row") {
      var r = el.closest(".bx-row");
      if (r) {
        var sec = r.closest(".bx-sec"), sl = locate(sec.dataset.id), a = side(r);
        var ri = sl.node.rows.findIndex(function (x) { return x.id === r.dataset.id; });
        return {level: "row", parentId: sec.dataset.id, index: ri + (a ? 1 : 0), mark: r, markClass: a ? "bx-dropline-after" : "bx-dropline"};
      }
      var sc = el.closest(".bx-sec");
      if (sc) return {level: "row", parentId: sc.dataset.id, index: locate(sc.dataset.id).node.rows.length, mark: sc, markClass: "bx-dropin"};
      return null;
    }
    var it = el.closest(".bx-el");
    if (it) {
      var col = it.closest(".bx-col"), cl = locate(col.dataset.id), a2 = side(it);
      var ei = cl.node.elements.findIndex(function (x) { return x.id === it.dataset.id; });
      return {level: "element", parentId: col.dataset.id, index: ei + (a2 ? 1 : 0), mark: it, markClass: a2 ? "bx-dropline-after" : "bx-dropline"};
    }
    var cc = el.closest(".bx-col");
    if (cc) return {level: "element", parentId: cc.dataset.id, index: locate(cc.dataset.id).node.elements.length, mark: cc, markClass: "bx-dropin"};
    return null;
  }
  function endDrag() {
    drag = null; clearMarks();
    document.querySelectorAll(".bx-dragging-src").forEach(function (n) { n.classList.remove("bx-dragging-src"); });
  }
  document.addEventListener("dragstart", function (e) {
    var src = e.target.closest && e.target.closest("[data-palette],[data-handle]");
    if (!src) return;
    if (src.hasAttribute("data-palette")) {
      var item = JSON.parse(src.dataset.palette);
      drag = {src: "palette", kind: item.kind, item: item};
    } else {
      drag = {src: "node", kind: src.dataset.kind, id: src.dataset.handle};
      var nodeEl = canvas.querySelector('[data-id="' + drag.id + '"]');
      if (nodeEl) { nodeEl.classList.add("bx-dragging-src"); try { e.dataTransfer.setDragImage(nodeEl, 12, 12); } catch (x) { /* بی‌اهمیت */ } }
    }
    e.dataTransfer.effectAllowed = "copyMove";
    e.dataTransfer.setData("text/plain", "ravix-builder");  // فایرفاکس بدون setData درگ را شروع نمی‌کند
  });
  canvas.addEventListener("dragover", function (e) {
    if (!drag) return;
    var t = resolveTarget(e);
    clearMarks();
    if (!t) { e.dataTransfer.dropEffect = "none"; return; }
    e.preventDefault(); e.dataTransfer.dropEffect = drag.src === "palette" ? "copy" : "move";
    if (t.mark) t.mark.classList.add(t.markClass);
  });
  canvas.addEventListener("drop", function (e) {
    if (!drag) return;
    e.preventDefault();
    var t = resolveTarget(e), d = drag;
    endDrag();
    if (!t) return;
    if (d.src === "palette") insertAt(t.level, t.parentId, t.index, paletteNode(d.item));
    else { var loc = locate(d.id); if (loc) insertAt(t.level, t.parentId, t.index, loc.node, d.id); }
  });
  document.addEventListener("dragend", endDrag);

  // ------------------------------------------------------------------ انتخاب، هاور، دکمه‌های ابزار
  canvas.addEventListener("click", function (e) {
    var act = e.target.closest("[data-act]");
    if (act) { e.stopPropagation(); nodeAction(act.dataset.act, act.dataset.id); return; }
    if (e.target.closest(".bx-chrome")) return;
    var n = e.target.closest("[data-id]");
    select(n ? n.dataset.id : null);
  });
  // تا وقتی دستگیره پایین نگه داشته شده، هاور عوض نمی‌شود؛ وگرنه نوار ابزار (و خود دستگیره) پیش از شروع درگ مخفی می‌شود.
  var pressing = false;
  canvas.addEventListener("mousedown", function (e) { if (e.target.closest("[data-handle]")) pressing = true; });
  document.addEventListener("mouseup", function () { pressing = false; });
  document.addEventListener("dragend", function () { pressing = false; });
  canvas.addEventListener("mouseover", function (e) {
    if (pressing || drag) return;
    canvas.querySelectorAll(".bx-hover").forEach(function (n) { n.classList.remove("bx-hover"); });
    var n = e.target.closest && e.target.closest("[data-id]");
    if (n) n.classList.add("bx-hover");
  });
  canvas.addEventListener("mouseleave", function () { canvas.querySelectorAll(".bx-hover").forEach(function (n) { n.classList.remove("bx-hover"); }); });
  function select(id) {
    selected = id;
    canvas.querySelectorAll(".bx-selected").forEach(function (n) { n.classList.remove("bx-selected"); });
    if (id) { var n = canvas.querySelector('[data-id="' + id + '"]'); if (n) n.classList.add("bx-selected"); }
    renderSettings();
  }
  function nodeAction(act, id) {
    var loc = locate(id); if (!loc) return;
    if (act === "up" || act === "down") {
      var to = loc.index + (act === "up" ? -1 : 1);
      if (to < 0 || to >= loc.arr.length) return;
      mutate(function () { var x = loc.arr.splice(loc.index, 1)[0]; loc.arr.splice(to, 0, x); });
    } else if (act === "dup") {
      if (loc.arr.length >= limitFor(levelOf(loc.type) === "column" ? "row" : levelOf(loc.type)) && loc.type !== "column") { toast(gettext("You have reached the limit."), true); return; }
      if (loc.type === "column") { toast(gettext("To add a column use the row settings."), true); return; }
      mutate(function () { var c = reId(clone(loc.node)); loc.arr.splice(loc.index + 1, 0, c); selected = c.id; });
    } else if (act === "del") {
      if (loc.type === "column" && loc.arr.length === 1) { toast(gettext("A row must have at least one column; delete the row itself."), true); return; }
      mutate(function () { loc.arr.splice(loc.index, 1); if (selected === id) selected = null; });
    }
  }
  document.addEventListener("keydown", function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || "")) || e.target.isContentEditable;
    var mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
    else if (mod && !typing && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
    else if (mod && !typing && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
    else if ((e.key === "Delete") && !typing && selected && mode === "edit" && !document.querySelector("dialog[open]")) { nodeAction("del", selected); }
  });
  $("#bx-undo").addEventListener("click", undo);
  $("#bx-redo").addEventListener("click", redo);

  // ------------------------------------------------------------------ پالت
  function buildPalette() {
    var st = $("#bx-palette-structure"), el = $("#bx-palette-elements");
    function item(data, label, ico) {
      var b = h("button", {type: "button", "class": "bx-pal", draggable: "true", "data-palette": JSON.stringify(data), "data-testid": "pal-" + (data.type || data.kind + (data.spans ? "-" + data.spans.join("-") : ""))}, icon(ico), h("span", {text: label}));
      b.addEventListener("click", function () { addByClick(data); });
      return b;
    }
    st.append(item({kind: "section"}, gettext("Section"), "section"));
    ROW_PRESETS.forEach(function (sp) {
      var label = sp.length === 1 ? gettext("1-column row") : interpolate(gettext("%(n)s-column row"), {n: fa(sp.length)}, true) + (sp[0] !== sp[1] ? " (" + fa(sp.join("+")) + ")" : "");
      st.append(item({kind: "row", spans: sp}, label, "row"));
    });
    [["heading", gettext("Heading")], ["text", gettext("Text")], ["image", gettext("Image")], ["button", gettext("Button")]].forEach(function (x) {
      el.append(item({kind: "element", type: x[0]}, x[1], x[0]));
    });
  }

  // ------------------------------------------------------------------ پنل تنظیمات
  function setProp(node, type, name, value, key) {
    mutate(function () { node.props[name] = value; }, {key: type + node.id + name, settings: false});
  }
  function colorField(f, node, type) {
    var cur = node.props[f.name];
    var txt = h("input", {type: "text", "class": "bx-in", dir: "ltr", placeholder: gettext("Default"), value: cur || "", maxlength: "7", "data-testid": "f-" + f.name});
    var pick = h("input", {type: "color", value: /^#[0-9a-f]{6}$/i.test(cur) ? cur : "#ffffff", "aria-label": interpolate(gettext("%(label)s (picker)"), {label: f.label}, true)});
    var clear = h("button", {type: "button", "class": "bx-mini", text: gettext("Default")});
    var err = h("div", {"class": "bx-err", hidden: true, text: gettext("The hex color is not valid (e.g. #1d4ed8).")});
    txt.addEventListener("input", function () {
      var v = txt.value.trim(); var ok = v === "" || /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v);
      txt.classList.toggle("is-bad", !ok); err.hidden = ok;
      if (ok) { if (/^#[0-9a-f]{6}$/i.test(v)) pick.value = v; setProp(node, type, f.name, v.toLowerCase()); }
    });
    pick.addEventListener("input", function () { txt.value = pick.value; txt.classList.remove("is-bad"); err.hidden = true; setProp(node, type, f.name, pick.value); });
    clear.addEventListener("click", function () { txt.value = ""; txt.classList.remove("is-bad"); err.hidden = true; setProp(node, type, f.name, ""); });
    return [h("div", {"class": "bx-row2"}, pick, txt, clear), err];
  }
  function intField(f, node, type) {
    var max = f.max;
    if (type === "column" && f.name === "span") {
      var loc = locate(node.id), others = loc.arr.reduce(function (a, c) { return a + (c === node ? 0 : c.props.span); }, 0);
      max = Math.max(f.min, Math.min(f.max, 12 - others));
    }
    var range = h("input", {type: "range", min: f.min, max: max, value: node.props[f.name], "aria-label": f.label});
    var num = h("input", {type: "number", "class": "bx-in", min: f.min, max: max, value: node.props[f.name], "data-testid": "f-" + f.name});
    function commit(v) {
      v = parseInt(v, 10); if (isNaN(v)) return;
      var clamped = Math.max(f.min, Math.min(max, v));
      if (clamped !== v) num.value = clamped;  // مقدار خارج از بازه (مثلاً مجموع ستون‌ها > ۱۲) همان لحظه اصلاح می‌شود
      range.value = clamped; setProp(node, type, f.name, clamped);
    }
    range.addEventListener("input", function () { num.value = range.value; commit(range.value); });
    num.addEventListener("input", function () { if (num.value !== "") commit(num.value); });
    num.addEventListener("blur", function () { num.value = node.props[f.name]; range.value = node.props[f.name]; });
    return [h("div", {"class": "bx-row2"}, range, num)];
  }
  function mediaField(f, node, type) {
    var m = node.props[f.name] ? mediaCache[node.props[f.name]] : null;
    var out = [];
    if (m && m.url) out.push(h("img", {"class": "bx-thumb", src: m.url, alt: ""}));
    var pick = h("button", {type: "button", "class": "bx-mini", "data-testid": "pick-media", text: node.props[f.name] ? gettext("Change image") : gettext("Choose from library")});
    pick.addEventListener("click", function () {
      openMedia(function (item) { mediaCache[item.id] = item; mutate(function () { node.props[f.name] = item.id; if (type === "image" && !node.props.alt && item.alt) node.props.alt = item.alt; }); });
    });
    var row = h("div", {"class": "bx-actions"}, pick);
    if (node.props[f.name]) {
      var rm = h("button", {type: "button", "class": "bx-mini danger", text: gettext("Remove image")});
      rm.addEventListener("click", function () { mutate(function () { node.props[f.name] = null; }); });
      row.append(rm);
    }
    out.push(row);
    return out;
  }
  function control(f, node, type) {
    var v = node.props[f.name], id = "f_" + f.name;
    if (f.kind === "text") {
      var i = h("input", {type: "text", "class": "bx-in", id: id, value: v, maxlength: f.max_len, "data-testid": "f-" + f.name});
      i.addEventListener("input", function () { setProp(node, type, f.name, i.value); });
      return [i];
    }
    if (f.kind === "textarea") {
      var t = h("textarea", {"class": "bx-in", id: id, rows: "6", maxlength: f.max_len, "data-testid": "f-" + f.name}); t.value = v;
      t.addEventListener("input", function () { setProp(node, type, f.name, t.value); });
      return [t];
    }
    if (f.kind === "url") {
      var u = h("input", {type: "text", "class": "bx-in", id: id, value: v, dir: "ltr", maxlength: "500", placeholder: gettext("/about/  or  https://…"), "data-testid": "f-" + f.name});
      var err = h("div", {"class": "bx-err", hidden: true, text: gettext("The address is not allowed; use an internal path (/…), http(s), mailto or tel.")});
      u.addEventListener("input", function () {
        var val = u.value.trim(), ok = (val === "" && !f.required) || isSafeUrl(val);
        u.classList.toggle("is-bad", !ok); err.hidden = ok;
        if (ok) setProp(node, type, f.name, val);
      });
      return [u, err];
    }
    if (f.kind === "choice") {
      var s = h("select", {"class": "bx-in", id: id, "data-testid": "f-" + f.name});
      f.choices.forEach(function (c) { s.append(h("option", {value: c[0], text: c[1], selected: c[0] === v})); });
      s.addEventListener("change", function () { mutate(function () { node.props[f.name] = s.value; }, {settings: false}); });
      return [s];
    }
    if (f.kind === "bool") {
      var c = h("input", {type: "checkbox", id: id, checked: !!v, "data-testid": "f-" + f.name});
      c.addEventListener("change", function () { mutate(function () { node.props[f.name] = c.checked; }, {settings: false}); });
      return [c];
    }
    if (f.kind === "color") return colorField(f, node, type);
    if (f.kind === "int") return intField(f, node, type);
    if (f.kind === "media") return mediaField(f, node, type);
    return [];
  }
  function fieldGroup(title, fields, node, type) {
    if (!fields.length) return null;
    var fs = h("fieldset", {"class": "bx-fs"}, h("legend", {text: title}));
    fields.forEach(function (f) {
      var wrap = h("div", {"class": "bx-field"});
      var lab = h("label", {"for": "f_" + f.name, text: f.label});
      if (f.kind === "bool") { wrap.className = "bx-field bx-check"; wrap.append(control(f, node, type)[0], lab); }
      else { wrap.append(lab); control(f, node, type).forEach(function (c) { wrap.append(c); }); }
      if (f.help) wrap.append(h("div", {"class": "bx-hint", text: f.help}));
      fs.append(wrap);
    });
    return fs;
  }
  function renderSettings() {
    settingsEl.replaceChildren();
    if (!selected) {
      settingsEl.append(h("h2", {text: gettext("Page settings")}), h("p", {"class": "bx-hint", text: gettext("To edit, click a section, row, column or element on the canvas.")}));
      var box = {props: doc.meta};
      var fs = h("fieldset", {"class": "bx-fs"}, h("legend", {text: gettext("General")}));
      META.forEach(function (f) {
        var c = h("input", {type: "checkbox", id: "f_" + f.name, checked: !!doc.meta[f.name], "data-testid": "f-" + f.name});
        c.addEventListener("change", function () { mutate(function () { doc.meta[f.name] = c.checked; }, {settings: false}); });
        fs.append(h("div", {"class": "bx-field bx-check"}, c, h("label", {"for": "f_" + f.name, text: f.label})));
      });
      settingsEl.append(fs);
      return;
    }
    var loc = locate(selected), node = loc.node, type = loc.type;
    var crumbs = h("div", {"class": "bx-crumbs", role: "navigation", "aria-label": gettext("Selection path")});
    loc.anc.concat([node]).forEach(function (n) {
      var tt = n === node ? type : (n.rows ? "section" : n.columns ? "row" : "column");
      var b = h("button", {type: "button", text: TYPE_LABEL[tt], "aria-current": n === node ? "true" : null});
      b.addEventListener("click", function () { select(n.id); });
      crumbs.append(b);
    });
    settingsEl.append(crumbs, h("h2", {text: interpolate(gettext("%(type)s settings"), {type: TYPE_LABEL[type]}, true)}));
    var fields = SPECS[type];
    [[gettext("Content"), "content"], [gettext("Layout and spacing"), "layout"], [gettext("Appearance"), "style"]].forEach(function (g) {
      var fs = fieldGroup(g[0], fields.filter(function (f) { return f.group === g[1]; }), node, type);
      if (fs) settingsEl.append(fs);
    });
    if (type === "row") {
      var acts = h("div", {"class": "bx-actions"});
      var add = h("button", {type: "button", "class": "bx-mini", text: gettext("+ Add column"), "data-testid": "add-column"});
      add.addEventListener("click", function () {
        if (node.columns.length >= LIM.columns_per_row) { toast(interpolate(gettext("At most %(n)s columns per row."), {n: fa(LIM.columns_per_row)}, true), true); return; }
        mutate(function () { node.columns.push({id: nid(), props: defaultsOf("column"), elements: []}); equalize(node); });
      });
      var eq = h("button", {type: "button", "class": "bx-mini", text: gettext("Split columns equally")});
      eq.addEventListener("click", function () { mutate(function () { equalize(node); }); });
      acts.append(add, eq); settingsEl.append(acts);
    }
    if (type === "column") {
      var total = loc.arr.reduce(function (a, c) { return a + c.props.span; }, 0);
      settingsEl.append(h("p", {"class": "bx-hint", text: interpolate(gettext("Total width of this row’s columns: %(total)s of 12"), {total: fa(total)}, true)}));
    }
  }
  function equalize(row) {
    var spans = EQUAL[row.columns.length];
    row.columns.forEach(function (c, i) { c.props.span = spans[i]; });
  }

  // ------------------------------------------------------------------ گفتگوی رسانه
  var mediaDlg = $("#bx-dlg-media"), mediaPick = null, mediaNext = null;
  function loadMedia(append) {
    var q = $("#bx-media-q").value.trim();
    api(cfg.urls.media + "?q=" + encodeURIComponent(q) + (append && mediaNext ? "&offset=" + mediaNext : ""), "GET").then(function (res) {
      var grid = $("#bx-media-grid"); if (!append) grid.replaceChildren();
      (res.data.items || []).forEach(function (m) {
        var b = h("button", {type: "button", title: m.title, "data-media-id": m.id}, h("img", {src: m.url, alt: m.alt || m.title}));
        b.addEventListener("click", function () { mediaDlg.close(); if (mediaPick) mediaPick(m); });
        grid.append(b);
      });
      if (!grid.children.length) grid.append(h("p", {"class": "bx-hint", text: gettext("No image found. First upload an image to the media library.")}));
      mediaNext = res.data.next; $("#bx-media-more").hidden = !mediaNext;
    });
  }
  function openMedia(cb) { mediaPick = cb; $("#bx-media-q").value = ""; mediaNext = null; loadMedia(false); mediaDlg.showModal(); }
  $("#bx-media-search").addEventListener("submit", function (e) { e.preventDefault(); mediaNext = null; loadMedia(false); });
  $("#bx-media-more").addEventListener("click", function () { loadMedia(true); });
  document.addEventListener("click", function (e) { var c = e.target.closest("[data-close]"); if (c) c.closest("dialog").close(); });

  // ------------------------------------------------------------------ دستگاه و پیش‌نمایش
  function setDevice(d) {
    device = d; frame.dataset.device = d; canvas.dataset.device = d;
    document.querySelectorAll("[data-device]").forEach(function (b) {
      if (!b.matches("button")) return;
      var on = b.dataset.device === d; b.classList.toggle("is-on", on); b.setAttribute("aria-checked", on ? "true" : "false");
    });
  }
  document.querySelectorAll("button[data-device]").forEach(function (b) { b.addEventListener("click", function () { setDevice(b.dataset.device); }); });
  function setMode(m, version) {
    mode = m;
    $("#bx-mode-edit").classList.toggle("is-on", m === "edit"); $("#bx-mode-edit").setAttribute("aria-pressed", m === "edit");
    $("#bx-mode-preview").classList.toggle("is-on", m === "preview"); $("#bx-mode-preview").setAttribute("aria-pressed", m === "preview");
    if (m === "edit") { preview.hidden = true; canvas.hidden = false; preview.removeAttribute("src"); return; }
    save().then(function () {
      canvas.hidden = true; preview.hidden = false;
      preview.src = cfg.urls.preview + (version ? "?version=" + version : "?t=" + Date.now());
    });
  }
  $("#bx-mode-edit").addEventListener("click", function () { setMode("edit"); });
  $("#bx-mode-preview").addEventListener("click", function () { setMode("preview"); });
  $("#bx-save").addEventListener("click", function () {
    save().then(function (ok) { if (ok) toast(gettext("Saved.")); });
  });

  // ------------------------------------------------------------------ نسخه‌ها، تأیید و انتشار
  var noteDlg = $("#bx-dlg-note"), versionsDlg = $("#bx-dlg-versions"), noteAction = null;
  function applyServerState(data, replaceDoc) {
    versions = data.versions || versions; live = data.live; canPublish = data.canPublish;
    if (replaceDoc) { doc = data.document; revision = data.revision; undoStack = []; redoStack = []; dirty = false; selected = null; renderCanvas(); renderSettings(); syncHistoryButtons(); }
    updatePublishBtn(); renderVersions();
  }
  function updatePublishBtn() {
    var b = $("#bx-publish");
    b.textContent = canPublish ? gettext("Publish") : gettext("Submit for approval");
    b.title = canPublish ? gettext("Publish this layout on the site") : gettext("The layout is sent to an administrator for approval");
    $("#bx-unpublish").hidden = !(canPublish && live);
  }
  function askNote(title, desc, onOk) {
    $("#bx-dlg-note-t").textContent = title; $("#bx-dlg-note-d").textContent = desc; $("#bx-note").value = "";
    $("#bx-dlg-errors").hidden = true; noteAction = onOk; noteDlg.showModal();
  }
  function showErrors(data) {
    var box = $("#bx-dlg-errors"); box.replaceChildren(h("strong", {text: data.error || gettext("Error")}));
    if (data.errors && data.errors.length) { var ul = h("ul"); data.errors.slice(0, 8).forEach(function (e) { ul.append(h("li", {text: e.message})); }); box.append(ul); }
    box.hidden = false;
  }
  $("#bx-note-ok").addEventListener("click", function () {
    var btn = $("#bx-note-ok"); btn.disabled = true;
    save().then(function (ok) {
      if (!ok && dirty) { btn.disabled = false; showErrors({error: gettext("Saving the draft must succeed first.")}); return; }
      return noteAction($("#bx-note").value).then(function (res) {
        btn.disabled = false;
        if (res.data.ok) { noteDlg.close(); applyServerState(res.data, false); }
        else showErrors(res.data);
      });
    });
  });
  $("#bx-publish").addEventListener("click", function () {
    if (canPublish) askNote(gettext("Publish layout"), gettext("The current draft replaces the page content on the site. (The page’s own status — published or draft — is controlled in the “Pages” section.)"),
      function (note) { return api(cfg.urls.versions, "POST", {action: "publish", note: note}).then(function (r) { if (r.data.ok) toast(gettext("The layout was published.")); return r; }); });
    else askNote(gettext("Submit for approval"), gettext("It will be published on the site after an administrator approves it. Until then the current page is shown."),
      function (note) { return api(cfg.urls.versions, "POST", {action: "submit", note: note}).then(function (r) { if (r.data.ok) toast(gettext("Submitted for approval.")); return r; }); });
  });
  $("#bx-versions").addEventListener("click", function () {
    save().then(function () { return api(cfg.urls.state, "GET"); }).then(function (res) {
      if (res.data.ok) { versions = res.data.versions; live = res.data.live; renderVersions(); }
      versionsDlg.showModal();
    });
  });
  $("#bx-save-version").addEventListener("click", function () {
    save().then(function () { return api(cfg.urls.versions, "POST", {action: "save", note: ""}); }).then(function (r) {
      if (r.data.ok) { applyServerState(r.data, false); toast(gettext("The version was saved.")); } else toast(errorText(r.data), true);
    });
  });
  $("#bx-unpublish").addEventListener("click", function () {
    if (!confirm(gettext("Unpublish the layout? The page goes back to its previous text content."))) return;
    api(cfg.urls.versions, "POST", {action: "unpublish"}).then(function (r) { if (r.data.ok) { applyServerState(r.data, false); toast(gettext("The layout was unpublished.")); } else toast(errorText(r.data), true); });
  });
  function versionAction(number, action, note) {
    return api(cfg.urls.versionAction + number + "/", "POST", {action: action, note: note || ""});
  }
  function renderVersions() {
    var list = $("#bx-vlist"); list.replaceChildren();
    if (!versions.length) list.append(h("p", {"class": "bx-hint", text: gettext("No version has been saved yet.")}));
    versions.forEach(function (v) {
      var badges = h("span");
      if (v.is_live) badges.append(h("span", {"class": "bx-badge live", text: gettext("Published")}));
      badges.append(h("span", {"class": "bx-badge " + (v.state === "pending" ? "pending" : v.state === "rejected" ? "rejected" : ""), text: v.state_label}));
      if (v.is_auto) badges.append(h("span", {"class": "bx-badge", text: gettext("Automatic")}));
      var row = h("div", {"class": "bx-v", "data-version": v.number},
        h("div", {"class": "grow"}, h("strong", {text: interpolate(gettext("Version %(n)s"), {n: fa(v.number)}, true)}), badges, h("small", {text: v.created_by + " — " + v.created_at + (v.note ? " — " + v.note : "") + (v.review_note ? interpolate(gettext(" (reviewer: %(note)s)"), {note: v.review_note}, true) : "")})));
      var pv = h("button", {type: "button", "class": "bx-mini", text: gettext("Preview")});
      pv.addEventListener("click", function () { versionsDlg.close(); setMode("preview", v.number); });
      var rs = h("button", {type: "button", "class": "bx-mini", "data-testid": "restore-" + v.number, text: gettext("Restore")});
      rs.addEventListener("click", function () {
        if (!confirm(interpolate(gettext("Replace the current draft with version %(n)s? An automatic version of the current state will be saved."), {n: fa(v.number)}, true))) return;
        save().then(function () { return versionAction(v.number, "restore"); }).then(function (r) {
          if (!r.data.ok) { toast(errorText(r.data), true); return; }
          return api(cfg.urls.state, "GET").then(function (s) { applyServerState(s.data, true); setMode("edit"); versionsDlg.close(); toast(interpolate(gettext("Version %(n)s was restored to the draft."), {n: fa(v.number)}, true)); });
        });
      });
      row.append(pv, rs);
      if (canPublish && v.state === "pending") {
        var ap = h("button", {type: "button", "class": "bx-mini", "data-testid": "approve-" + v.number, text: gettext("Approve and publish")});
        ap.addEventListener("click", function () { versionAction(v.number, "approve").then(function (r) { if (r.data.ok) { applyServerState(r.data, false); toast(gettext("Approved and published.")); } else toast(errorText(r.data), true); }); });
        var rj = h("button", {type: "button", "class": "bx-mini danger", text: gettext("Reject")});
        rj.addEventListener("click", function () {
          var why = prompt(gettext("Reason for rejection (optional):")) || "";
          versionAction(v.number, "reject", why).then(function (r) { if (r.data.ok) { applyServerState(r.data, false); toast(gettext("The version was rejected.")); } else toast(errorText(r.data), true); });
        });
        row.append(ap, rj);
      }
      list.append(row);
    });
  }

  // ------------------------------------------------------------------ شروع
  buildPalette();
  setStatus(gettext("Loading…"));
  api(cfg.urls.state, "GET").then(function (res) {
    if (!res.data.ok) { setStatus(res.data.error || gettext("Loading failed"), "is-err"); return; }
    applyServerState(res.data, true);
    setStatus(gettext("Ready"), "is-ok");
  });
})();
