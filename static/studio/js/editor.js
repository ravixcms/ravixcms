/* Ravix Studio — the editor. Document model: core.js. Setting controls: inspector.js. Preview frame: ../preview.js.
   No framework and no build step; every string comes from the server (gettext) in the page's JSON config. */
(function () {
  "use strict";
  var C = window.StudioCore, I = window.StudioInspector, el = I.el;
  var cfgNode = document.getElementById("studio-config");
  if (!cfgNode || !C) return;
  var cfg = JSON.parse(cfgNode.textContent);
  var t = cfg.i18n, specs = cfg.specs, urls = cfg.urls, ORIGIN = location.origin;
  var rtl = document.documentElement.dir === "rtl";
  var $ = function (id) { return document.getElementById(id); };

  var S = {
    doc: C.emptyDocument(specs), rev: 0, selected: "", device: "desktop", versions: [], live: null, canPublish: !!cfg.canPublish,
    dirty: false, saving: false, saveAgain: false, previewReady: false, previewSeq: 0, history: new C.History(100),
    collapsed: {}, leftTab: "widgets", rightTab: "content", invalid: false, loaded: false, otherEditor: null, conflict: false
  };
  var timers = { save: 0, preview: 0, ping: 0 };
  var frame = $("stu-frame"), tree = $("stu-tree");
  var wIndex = {};
  specs.widgets.forEach(function (w) { w.specs = specs; wIndex[w.type] = w; });

  // ================================================================== helpers
  function announce(msg) { var live = $("stu-live"); live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); }
  function status(text, kind) { var s = $("stu-status"); s.textContent = text || ""; if (kind) s.setAttribute("data-kind", kind); else s.removeAttribute("data-kind"); }
  function fmt(s, a) { return String(s).replace("%s", a); }

  function api(url, method, body) {
    var opt = { method: method || "GET", credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.headers["X-CSRFToken"] = cfg.csrf; opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: t.saveFailed }; }).then(function (data) { data.status = r.status; return data; });
    });
  }

  function nodeSpec(f) {
    if (f.kind === "section") return specs.section;
    if (f.kind === "column") return specs.column;
    var w = wIndex[f.node.type];
    return w ? { fields: w.fields, style_fields: w.style_fields } : { fields: [], style_fields: [] };
  }
  function nodeLabel(f) { return C.label(f.node, f.kind, specs, t); }

  // ================================================================== mutation + history + autosave
  function snapshot() { return JSON.stringify(S.doc); }
  function mutate(fn, key, opts) {
    var before = snapshot();
    var result = fn(S.doc);
    if (result === false || (typeof result === "string" && result)) return result;
    if (snapshot() === before) return result;
    S.history.push(before, key);
    afterChange(opts || {});
    return result;
  }
  function afterChange(opts) {
    S.dirty = true; S.invalid = false;
    updateUndoButtons();
    status(t.unsaved);
    if (!opts.keepTree) renderTree();
    if (!opts.keepInspector) renderInspector();
    schedulePreview();
    scheduleSave();
  }
  function updateUndoButtons() { $("stu-undo").disabled = !S.history.canUndo(); $("stu-redo").disabled = !S.history.canRedo(); }
  function replaceDoc(raw) {
    S.doc = JSON.parse(raw);
    if (S.selected && !C.find(S.doc, S.selected)) S.selected = "";
    afterChange({});
    sendSelect(true);
  }
  function undo() { var s = S.history.undo(snapshot()); if (s) { replaceDoc(s); announce(t.undo); } }
  function redo() { var s = S.history.redo(snapshot()); if (s) { replaceDoc(s); announce(t.redo); } }

  function scheduleSave() { clearTimeout(timers.save); timers.save = setTimeout(saveDraft, 1500); }
  function saveDraft() {
    clearTimeout(timers.save);
    if (S.conflict) return Promise.resolve(false);
    if (S.saving) { S.saveAgain = true; return Promise.resolve(false); }
    if (!S.dirty) return Promise.resolve(true);
    S.saving = true; S.saveAgain = false;
    var sent = snapshot();
    status(t.saving);
    return api(urls.draft, "POST", { document: S.doc, revision: S.rev }).then(function (r) {
      S.saving = false;
      if (r.ok) {
        S.rev = r.revision;
        if (snapshot() === sent && !S.saveAgain) { S.dirty = false; status(t.saved, "saved"); } else scheduleSave();
        return true;
      }
      if (r.status === 409) { showConflict(r); return false; }
      if (r.status === 422) { S.invalid = true; status(t.invalid, "error"); showErrors(r.errors); return false; }
      status(r.error || t.saveFailed, "error");
      if (r.status !== 401 && r.status !== 403) scheduleSave();
      return false;
    }).catch(function () { S.saving = false; status(t.saveFailed, "error"); scheduleSave(); return false; });
  }
  function flush() { return S.dirty ? saveDraft() : Promise.resolve(true); }

  // ================================================================== banners / conflicts / presence
  function banner(key, kind, content, actions) {
    var host = $("stu-banners"), old = host.querySelector('[data-key="' + key + '"]');
    if (old) old.remove();
    if (content === null) return;
    var b = el("div", { class: "stu-banner", "data-kind": kind, "data-key": key, role: kind === "error" ? "alert" : "status" });
    if (typeof content === "string") b.appendChild(el("span", { text: content })); else b.appendChild(content);
    (actions || []).forEach(function (a) { b.appendChild(el("button", { type: "button", class: "stu-btn", text: a[0], onclick: a[1] })); });
    host.appendChild(b);
  }
  function showErrors(errors) {
    if (!errors || !errors.length) return banner("errors", "error", null);
    var ul = el("ul");
    errors.slice(0, 8).forEach(function (e) { ul.appendChild(el("li", { text: e.message })); });
    banner("errors", "error", el("div", {}, [el("strong", { text: t.fixErrors }), ul]), [[t.close, function () { banner("errors", "error", null); }]]);
  }
  function showConflict(r) {
    S.conflict = true;
    var body = el("p", { text: t.conflictBody + (r.by ? " (" + r.by + ")" : "") });
    openDialog(t.conflictTitle, body, [
      [t.conflictLoad, function () { loadState(true).then(function () { S.conflict = false; }); }, ""],
      [t.conflictOverwrite, function () {
        S.rev = r.revision; S.conflict = false;
        api(urls.draft, "POST", { document: S.doc, revision: S.rev, overwrite: true }).then(function (x) {
          if (x.ok) { S.rev = x.revision; S.dirty = false; status(t.saved, "saved"); } else status(x.error || t.saveFailed, "error");
        });
      }, "stu-primary"]
    ], true);
  }
  function ping() {
    api(urls.ping, "POST", {}).then(function (r) {
      if (!r.ok) return;
      S.otherEditor = r.otherEditor;
      banner("other", "warn", r.otherEditor ? fmt(t.otherEditor, r.otherEditor.name) : null);
      if (r.revision > S.rev && !S.dirty && !S.saving) {
        banner("newer", "info", t.conflictBody + (r.updatedBy ? " (" + r.updatedBy + ")" : ""), [[t.conflictLoad.split(" (")[0], function () { banner("newer", "info", null); loadState(true); }]]);
      }
    });
  }

  // ================================================================== dialog
  function openDialog(title, body, buttons, modalOnly) {
    var d = $("stu-dialog");
    $("stu-dialog-title").textContent = title;
    var b = $("stu-dialog-body"), foot = $("stu-dialog-foot");
    b.textContent = ""; foot.textContent = "";
    if (body) b.appendChild(body);
    (buttons || []).forEach(function (x) {
      foot.appendChild(el("button", { type: "button", class: "stu-btn " + (x[2] || ""), text: x[0], onclick: function () { if (x[1]) x[1](); if (!x[3]) closeDialog(); } }));
    });
    if (!modalOnly) foot.appendChild(el("button", { type: "button", class: "stu-btn", text: t.close, onclick: closeDialog }));
    d.oncancel = function (e) { if (modalOnly) e.preventDefault(); };
    if (!d.open) d.showModal();
    var first = d.querySelector("input,select,textarea,button");
    if (first) first.focus();
    return d;
  }
  function closeDialog() { var d = $("stu-dialog"); if (d.open) d.close(); }

  // ================================================================== media
  var mediaCache = {};
  function mediaUrl(id, done) {
    if (mediaCache[id]) return done(mediaCache[id].url);
    api(urls.media + "?ids=" + encodeURIComponent(id)).then(function (r) {
      (r.items || []).forEach(function (m) { mediaCache[m.id] = m; });
      done(mediaCache[id] ? mediaCache[id].url : "");
    });
  }
  function pickMedia(kind, done) {
    var grid = el("div", { class: "stu-media-grid" }), more = el("button", { type: "button", class: "stu-btn", text: t.more });
    var q = el("input", { type: "search", "aria-label": t.search, placeholder: t.search, class: "stu-search" }), next = 0;
    q.style.cssText = "inline-size:100%;padding:.4rem .5rem";
    function load(reset) {
      if (reset) { grid.textContent = ""; next = 0; }
      api(urls.media + "?kind=" + kind + "&q=" + encodeURIComponent(q.value) + "&offset=" + next).then(function (r) {
        (r.items || []).forEach(function (m) {
          mediaCache[m.id] = m;
          var b = el("button", { type: "button", title: m.title, "aria-label": m.title }, [kind === "video" ? el("span", { text: m.title }) : el("img", { src: m.url, alt: "", loading: "lazy" })]);
          b.addEventListener("click", function () { closeDialog(); done(m); });
          grid.appendChild(b);
        });
        if (!grid.children.length) grid.appendChild(el("p", { class: "stu-help", text: t.noMedia }));
        next = r.next; more.hidden = r.next === null || r.next === undefined;
      });
    }
    var timer = 0;
    q.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(function () { load(true); }, 300); });
    more.addEventListener("click", function () { load(false); });
    openDialog(kind === "video" ? t.chooseVideo : t.chooseImage, el("div", {}, [q, grid, more]));
    load(true);
  }

  // ================================================================== preview
  function schedulePreview() { clearTimeout(timers.preview); timers.preview = setTimeout(renderPreview, 250); }
  function renderPreview() {
    if (!S.previewReady) return;
    var seq = ++S.previewSeq;
    $("stu-preview-busy").hidden = false;
    api(urls.render, "POST", { document: S.doc }).then(function (r) {
      if (seq !== S.previewSeq) return;
      $("stu-preview-busy").hidden = true;
      if (r.ok) { banner("preview", "error", null); frame.contentWindow.postMessage({ st: "render", html: r.html, css: r.css, selected: S.selected }, ORIGIN); }
      else if (r.status === 422) showErrors(r.errors);
      else banner("preview", "error", t.previewFailed);
    }).catch(function () { $("stu-preview-busy").hidden = true; banner("preview", "error", t.previewFailed); });
  }
  function sendSelect(scroll) { if (S.previewReady) frame.contentWindow.postMessage({ st: "select", id: S.selected, scroll: !!scroll }, ORIGIN); }

  window.addEventListener("message", function (e) {
    if (e.origin !== ORIGIN || e.source !== frame.contentWindow) return;
    var m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.st === "ready") { S.previewReady = true; renderPreview(); }
    else if (m.st === "select") select(m.id, { fromPreview: true });
    else if (m.st === "move") { var why = doMove(m.id, m.owner, m.index); if (why) { announce(reason(why)); renderPreview(); } }
    else if (m.st === "drop") addWidget(m.widget, m.owner, m.index);
    else if (m.st === "key") handleKey({ key: m.key, ctrlKey: m.ctrl, shiftKey: m.shift, altKey: m.alt, target: document.body, preventDefault: function () {} });
    else if (m.st === "focus-editor") { var sel = tree.querySelector('[tabindex="0"]'); if (sel) sel.focus(); }
  });

  // ================================================================== selection / structure operations
  function select(id, opts) {
    opts = opts || {};
    S.selected = id || "";
    renderTree(); renderInspector();
    if (!opts.fromPreview) sendSelect(true);
    else sendSelect(false);
    if (S.selected) { var f = C.find(S.doc, S.selected); if (f) announce(fmt(t.selected, nodeLabel(f))); }
  }
  function reason(code) {
    return ({ sections: specs.limits.sections + " ✕", columns: specs.limits.columns_per_section + " ✕", widgets: specs.limits.widgets_per_column + " ✕", depth: "✕", nodes: specs.limits.total_nodes + " ✕", inside: "✕" })[code] || "✕";
  }
  function targetList(forKind) {  // where a click-added item goes, from the current selection
    var f = S.selected ? C.find(S.doc, S.selected) : null;
    if (forKind === "section") {
      if (!f) return { owner: "root", index: S.doc.sections.length };
      var top = f; while (top && top.parentId !== "root") top = C.find(S.doc, top.parentId);
      return { owner: "root", index: top ? top.index + 1 : S.doc.sections.length };
    }
    if (!f) { var last = S.doc.sections[S.doc.sections.length - 1]; if (!last) return null; return { owner: last.columns[0].id, index: last.columns[0].widgets.length }; }
    if (f.kind === "column") return { owner: f.node.id, index: f.node.widgets.length };
    if (f.kind === "widget") return { owner: f.parentId, index: f.index + 1 };
    return { owner: f.node.columns[0].id, index: f.node.columns[0].widgets.length };  // section
  }
  function addSection(spans) {
    var node = C.makeSection(specs, spans), at = targetList("section"), why = "";
    mutate(function (d) { why = C.insert(d, specs, at.owner, at.index, node, "section"); return why || undefined; });
    if (why) return announce(reason(why));
    select(node.id); announce(fmt(t.added, t.section));
  }
  function addWidget(type, owner, index) {
    var w = wIndex[type];
    if (!w || (w.admin_only && !cfg.canHtml)) return;
    var at = owner ? { owner: owner, index: index } : targetList("widget");
    if (!at) { addSection([12]); at = targetList("widget"); }
    var node = C.makeWidget(w), why = "";
    mutate(function (d) { why = C.insert(d, specs, at.owner, at.index, node, "widget"); return why || undefined; });
    if (why) return announce(reason(why));
    select(node.id); announce(fmt(t.added, w.title));
  }
  function addColumn(sectionId) {
    var why = "", node = C.makeColumn(specs);
    mutate(function (d) { var L = C.listOf(d, sectionId); why = C.insert(d, specs, sectionId, L.items.length, node, "column"); return why || undefined; });
    if (why) announce(reason(why)); else select(node.id);
  }
  function doMove(id, owner, index) {
    var why = "";
    mutate(function (d) { why = C.move(d, specs, id, owner, index); return why || undefined; });
    if (!why) { announce(t.movedTo); select(id, { fromPreview: true }); }
    return why;
  }
  function stepNode(id, dir, mode) {
    var why = "";
    mutate(function (d) { why = C.step(d, specs, id, dir, mode); return why || undefined; });
    if (!why) announce(t.movedTo); else if (why !== "edge") announce(reason(why));
    focusTreeItem(id);
  }
  function duplicateNode(id) {
    var res;
    mutate(function (d) { res = C.duplicate(d, specs, id); return res.error || undefined; });
    if (res && res.id) { select(res.id); announce(t.duplicated); } else if (res) announce(reason(res.error));
  }
  function deleteNode(id, confirmFirst) {
    var f = C.find(S.doc, id);
    if (!f) return;
    var hasKids = (f.node.columns && f.node.columns.length) || (f.node.widgets && f.node.widgets.length);
    var go = function () {
      var next = f.list[f.index + 1] || f.list[f.index - 1];
      mutate(function (d) { return C.remove(d, id) ? undefined : false; });
      select(next ? next.id : (f.parentId !== "root" ? f.parentId : ""));
      announce(t.deleted);
      focusTreeItem(S.selected);
    };
    if (confirmFirst && hasKids && (f.kind !== "widget" || f.node.columns)) openDialog(t.delete, el("p", { text: t.confirmDelete }), [[t.delete, go, "stu-primary"]]);
    else go();
  }

  // ================================================================== palette
  function svgIcon(name) {
    var d = (window.__studioIcons || {})[name];
    var ns = "http://www.w3.org/2000/svg", s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    if (d) s.innerHTML = d;
    else { var c = document.createElementNS(ns, "rect"); c.setAttribute("x", 4); c.setAttribute("y", 4); c.setAttribute("width", 16); c.setAttribute("height", 16); c.setAttribute("rx", 3); s.appendChild(c); }
    return s;
  }
  var CATEGORIES = [["basic", t.catBasic], ["media", t.catMedia], ["content", t.catContent], ["layout", t.catLayout], ["plugin", t.catPlugin]];
  function buildPalette() {
    var host = $("stu-palette");
    host.textContent = "";
    $("stu-drag-hint").textContent = t.dragHint;
    $("stu-keys").textContent = t.keyboard;
    var pre = el("div", { class: "stu-presets", role: "group", "aria-label": t.layoutPresets });
    [[[12], t.oneColumn], [[6, 6], t.twoColumns], [[4, 4, 4], t.threeColumns], [[3, 3, 3, 3], t.fourColumns], [[8, 4], "8 / 4"], [[4, 8], "4 / 8"]].forEach(function (p) {
      var b = el("button", { type: "button", title: t.addSection + ": " + p[1], "aria-label": t.addSection + ": " + p[1] }, []);
      p[0].forEach(function (span) { var i = el("i"); i.style.flex = String(span); b.appendChild(i); });
      b.addEventListener("click", function () { addSection(p[0]); });
      pre.appendChild(b);
    });
    host.appendChild(el("div", { class: "stu-pal-group" }, [el("h3", { text: t.addSection }), pre]));
    var cats = {};
    specs.widgets.forEach(function (w) { (cats[w.category] = cats[w.category] || []).push(w); });
    Object.keys(cats).forEach(function (cat) {
      var grid = el("div", { class: "stu-pal-grid" });
      cats[cat].forEach(function (w) {
        var locked = w.admin_only && !cfg.canHtml;
        var b = el("button", { type: "button", class: "stu-pal-item", draggable: locked ? null : "true", "data-widget": w.type, "aria-disabled": locked ? "true" : null,
          title: locked ? t.adminOnly : (w.description || w.title) }, [svgIcon(w.icon), el("span", { text: w.title }), locked ? el("small", { text: t.adminOnly }) : null]);
        b.addEventListener("click", function () { if (!locked) addWidget(w.type); });
        b.addEventListener("dragstart", function (e) {
          e.dataTransfer.setData("text/ravix-studio", w.type); e.dataTransfer.setData("text/plain", w.title); e.dataTransfer.effectAllowed = "copy";
          document.body.classList.add("st-dragging");
        });
        b.addEventListener("dragend", function () { document.body.classList.remove("st-dragging"); });
        grid.appendChild(b);
      });
      host.appendChild(el("div", { class: "stu-pal-group" }, [el("h3", { text: (CATEGORIES.filter(function (c) { return c[0] === cat; })[0] || [0, cat])[1] }), grid]));
    });
  }

  // ================================================================== layers panel (Navigator)
  var sortables = [];
  function renderTree() {
    var focusedId = document.activeElement && tree.contains(document.activeElement) ? document.activeElement.getAttribute("data-id") : null;
    sortables.forEach(function (s) { s.destroy(); }); sortables = [];
    tree.textContent = "";
    if (!S.doc.sections.length) { tree.appendChild(el("p", { class: "stu-node-empty", text: t.emptyPage })); }
    else tree.appendChild(treeList(S.doc.sections, "section", "root", 1));
    tree.appendChild(el("button", { type: "button", class: "stu-btn stu-add-section", text: "+ " + t.addSection, onclick: function () { addSection([12]); } }));
    var items = tree.querySelectorAll('[role="treeitem"]');
    var want = S.selected && tree.querySelector('[data-id="' + S.selected + '"]') || items[0];
    Array.prototype.forEach.call(items, function (it) { it.tabIndex = it === want ? 0 : -1; });
    if (focusedId) { var again = tree.querySelector('[data-id="' + focusedId + '"]'); if (again) again.focus(); }
    if (window.Sortable) {
      Array.prototype.forEach.call(tree.querySelectorAll("ul[data-list]"), function (ul) {
        var kind = ul.getAttribute("data-list");
        sortables.push(window.Sortable.create(ul, {
          group: "tree-" + kind, animation: 120, handle: ".stu-grip", draggable: "li", ghostClass: "sortable-ghost", fallbackOnBody: true,
          onEnd: function (evt) {
            if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;
            doMove(evt.item.getAttribute("data-id"), evt.to.getAttribute("data-owner"), evt.newIndex);
          }
        }));
      });
    }
  }
  function treeList(items, kind, owner, level) {
    var ul = el("ul", { role: level === 1 ? "group" : "group", "data-list": kind, "data-owner": owner });
    items.forEach(function (n) {
      var f = { node: n, kind: kind }, kids = kind === "section" ? n.columns : kind === "column" ? n.widgets : n.columns || null;
      var open = !S.collapsed[n.id], label = nodeLabel(f);
      var li = el("li", { role: "treeitem", "data-id": n.id, "data-kind": kind, "aria-level": level, "aria-selected": n.id === S.selected ? "true" : "false",
        "aria-expanded": kids && kids.length ? String(open) : null, "aria-label": label, tabindex: "-1" });
      var row = el("div", { class: "stu-node", "aria-selected": n.id === S.selected ? "true" : "false" });
      row.appendChild(el("span", { class: "stu-grip", "aria-hidden": "true", title: t.moveUp + " / " + t.moveDown }, [svgIcon("grip")]));
      if (kids && kids.length) row.appendChild(el("button", { type: "button", class: "stu-toggle", tabindex: "-1", "aria-hidden": "true", text: open ? "▾" : (rtl ? "◂" : "▸"),
        onclick: function (e) { e.stopPropagation(); S.collapsed[n.id] = open; renderTree(); } }));
      else row.appendChild(el("span", { class: "stu-toggle", "aria-hidden": "true" }));
      row.appendChild(el("span", { class: "stu-name", text: label }));
      if (kind === "widget" && n.props && n.style && hiddenOn(n)) row.appendChild(el("span", { class: "stu-kind", text: "◌", title: t.hiddenOnThis }));
      row.addEventListener("click", function () { select(n.id); });
      li.appendChild(row);
      if (kids && kids.length && open) {
        var childKind = kind === "section" ? "column" : kind === "column" ? "widget" : "column";
        li.appendChild(treeList(kids, childKind, n.id, level + 1));
      } else if (kind === "column" && !n.widgets.length) {
        li.appendChild(el("div", { class: "stu-node-empty", text: t.emptyColumn }));
      }
      ul.appendChild(li);
    });
    return ul;
  }
  function hiddenOn(n) { var h = C.styleValue(n, S.device, "hidden"); return h.value === true; }
  function focusTreeItem(id) {
    if (!id) return;
    var it = tree.querySelector('[data-id="' + id + '"]');
    if (it) { Array.prototype.forEach.call(tree.querySelectorAll('[role="treeitem"]'), function (x) { x.tabIndex = -1; }); it.tabIndex = 0; it.focus(); }
  }
  tree.addEventListener("keydown", function (e) {
    var item = e.target.closest && e.target.closest('[role="treeitem"]');
    if (!item || e.target !== item) return;
    var id = item.getAttribute("data-id"), f = C.find(S.doc, id);
    if (!f) return;
    var visible = Array.prototype.slice.call(tree.querySelectorAll('[role="treeitem"]')), at = visible.indexOf(item);
    var forward = rtl ? "ArrowLeft" : "ArrowRight", back = rtl ? "ArrowRight" : "ArrowLeft";
    var handled = true;
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) stepNode(id, e.key === "ArrowUp" ? -1 : 1, "list");
    else if (e.altKey && (e.key === forward || e.key === back) && f.kind === "widget") stepNode(id, e.key === forward ? 1 : -1, "column");
    else if (e.key === "ArrowDown") { if (visible[at + 1]) focusTreeItem(visible[at + 1].getAttribute("data-id")); }
    else if (e.key === "ArrowUp") { if (visible[at - 1]) focusTreeItem(visible[at - 1].getAttribute("data-id")); }
    else if (e.key === "Home") focusTreeItem(visible[0].getAttribute("data-id"));
    else if (e.key === "End") focusTreeItem(visible[visible.length - 1].getAttribute("data-id"));
    else if (e.key === forward) { if (item.getAttribute("aria-expanded") === "false") { S.collapsed[id] = false; renderTree(); focusTreeItem(id); } else if (visible[at + 1] && visible[at + 1].getAttribute("aria-level") > item.getAttribute("aria-level")) focusTreeItem(visible[at + 1].getAttribute("data-id")); }
    else if (e.key === back) { if (item.getAttribute("aria-expanded") === "true") { S.collapsed[id] = true; renderTree(); focusTreeItem(id); } else if (f.parentId !== "root") focusTreeItem(f.parentId); }
    else if (e.key === "Enter" || e.key === " ") { select(id); var first = $("stu-inspector").querySelector("input,select,textarea,button"); if (e.key === "Enter" && first) first.focus(); }
    else if (e.key === "Delete" || e.key === "Backspace") deleteNode(id, true);
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") duplicateNode(id);
    else handled = false;
    if (handled) e.preventDefault();
  });
  tree.addEventListener("focusin", function (e) { var it = e.target.closest && e.target.closest('[role="treeitem"]'); if (it && e.target === it && it.getAttribute("data-id") !== S.selected) select(it.getAttribute("data-id")); });

  // ================================================================== inspector
  var inspectorMemory = {};
  function renderInspector() {
    var host = $("stu-inspector"), tabs = $("stu-right-tabs"), head = $("stu-insp-title"), actions = $("stu-insp-actions");
    var keep = document.activeElement && host.contains(document.activeElement) ? document.activeElement.id : "";
    host.textContent = ""; tabs.textContent = ""; actions.textContent = "";
    var f = S.selected ? C.find(S.doc, S.selected) : null;
    if (!f) { S.inspected = ""; head.textContent = t.pageSettings; return pageSettings(host, tabs); }
    head.textContent = nodeLabel(f);
    [["duplicate", t.duplicate, function () { duplicateNode(S.selected); }], ["delete", t.delete, function () { deleteNode(S.selected, true); }]].forEach(function (a) {
      actions.appendChild(el("button", { type: "button", "data-action": a[0], class: "stu-btn" + (a[0] === "delete" ? " stu-danger" : ""), text: a[1], onclick: a[2] }));
    });
    if (f.kind === "section") actions.appendChild(el("button", { type: "button", class: "stu-btn", text: t.exportSection, onclick: function () { exportFile(S.selected); } }));
    var sp = nodeSpec(f);
    var tabDefs = [["content", t.content], ["style", t.style], ["layout", t.layout]];
    if (f.kind !== "widget") tabDefs = [["layout", t.layout], ["style", t.style]];
    if (S.inspected !== f.node.id || !tabDefs.some(function (x) { return x[0] === S.rightTab; })) S.rightTab = tabDefs[0][0];
    S.inspected = f.node.id;
    tabDefs.forEach(function (d) {
      tabs.appendChild(el("button", { type: "button", role: "tab", "aria-selected": d[0] === S.rightTab ? "true" : "false", text: d[1], onclick: function () { S.rightTab = d[0]; renderInspector(); } }));
    });
    var wspec = f.kind === "widget" ? wIndex[f.node.type] : null;
    if (f.kind === "widget" && !wspec) { host.appendChild(el("p", { class: "stu-hint", text: t.unavailable })); return; }
    // content-type settings (the same on every screen)
    (sp.fields || []).forEach(function (fd) {
      var tab = fd.group === "content" ? "content" : fd.group === "style" ? "style" : "layout";
      if (f.kind !== "widget" && tab === "content") tab = "layout";
      if (tab !== S.rightTab) return;
      host.appendChild(I.field(fd, {
        value: f.node.props[fd.name], t: t, icons: specs.icons, canHtml: cfg.canHtml, pickMedia: pickMedia, mediaUrl: mediaUrl,
        onChange: function (v) { mutate(function (d) { C.setProp(d, S.selected, fd.name, v); }, "p:" + S.selected + ":" + fd.name, { keepInspector: true }); }
      }));
    });
    // responsive style settings
    var styleTab = S.rightTab === "content" ? null : S.rightTab;
    if (styleTab) {
      var title = el("div", { class: "stu-group-title", text: t.style + " — " + t[S.device] });
      var any = false, frag = document.createDocumentFragment();
      (sp.style_fields || []).forEach(function (fd) {
        if ((fd.group === "layout" ? "layout" : "style") !== styleTab) return;
        any = true;
        var own = Object.prototype.hasOwnProperty.call((f.node.style || {})[S.device] || {}, fd.name);
        var cur = C.styleValue(f.node, S.device, fd.name);
        var inherited = !own && cur.from;
        var shown = own ? cur.value : (fd.kind === "color" || fd.kind === "media" ? "" : inherited ? cur.value : "");
        frag.appendChild(I.field(fd, {
          value: own ? cur.value : (inherited ? cur.value : ""), allowEmpty: true, t: t, icons: specs.icons, canHtml: cfg.canHtml, pickMedia: pickMedia, mediaUrl: mediaUrl,
          placeholder: inherited ? String(cur.value === true ? t.yes : cur.value === false ? t.no : cur.value) : (S.device === "desktop" ? "" : t.inheritsFrom.split("=")[0].trim()),
          from: inherited ? "· " + t[cur.from] : "", explicit: own, canReset: true,
          rebuild: renderInspector,
          onChange: function (v) {
            var dev = S.device;
            mutate(function (d) { C.setStyle(d, S.selected, dev, fd.name, v === undefined ? "" : v); }, "s:" + S.selected + ":" + dev + ":" + fd.name, { keepInspector: true });
            if (v === "" || own === false) renderInspectorSoon();
          }
        }));
        void shown;
      });
      if (any) { host.appendChild(title); host.appendChild(frag); }
    }
    if (keep) { var again = document.getElementById(keep); if (again) again.focus(); }
  }
  var inspectorTimer = 0;
  function renderInspectorSoon() { clearTimeout(inspectorTimer); inspectorTimer = setTimeout(renderInspector, 400); }
  function pageSettings(host, tabs) {
    tabs.appendChild(el("button", { type: "button", role: "tab", "aria-selected": "true", text: t.settings }));
    (specs.meta || []).forEach(function (fd) {
      host.appendChild(I.field(fd, { value: S.doc.meta[fd.name], t: t, onChange: function (v) { mutate(function (d) { d.meta[fd.name] = v; }, "m:" + fd.name, { keepInspector: true }); } }));
    });
    host.appendChild(el("p", { class: "stu-hint", text: t.dragHint }));
  }

  // ================================================================== devices
  function setDevice(name) {
    S.device = name;
    $("stu-frame-wrap").setAttribute("data-device", name);
    Array.prototype.forEach.call(document.querySelectorAll("#stu-devices [data-device]"), function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-device") === name ? "true" : "false"); });
    renderInspector(); renderTree();
    announce(t[name]);
  }

  // ================================================================== versions / publishing
  function postVersion(action, note) {
    return flush().then(function (ok) {
      if (!ok && S.invalid) return null;
      return api(urls.versions, "POST", { action: action, note: note || "" });
    }).then(function (r) {
      if (!r) return null;
      if (r.ok) { applyState(r, false); status(t.saved, "saved"); banner("errors", "error", null); }
      else { status(r.error || t.saveFailed, "error"); if (r.errors) showErrors(r.errors); else banner("errors", "error", r.error || t.saveFailed); }
      return r;
    });
  }
  function noteDialog(title, label, action, primary) {
    var inp = el("input", { type: "text", maxlength: 200, placeholder: t.noteOptional, "aria-label": t.noteOptional });
    inp.style.cssText = "inline-size:100%;padding:.45rem .6rem";
    openDialog(title, el("div", {}, [el("p", { text: label || "" }), inp]), [[primary || title, function () { postVersion(action, inp.value); }, "stu-primary"]]);
  }
  function refreshPublishButton() {
    var b = $("stu-publish"), pending = S.versions.some(function (v) { return v.state === "pending"; });
    b.textContent = S.canPublish ? t.publish : t.submit;
    b.setAttribute("data-mode", S.canPublish ? "publish" : "submit");
    b.disabled = !S.canPublish && pending;
    b.title = !S.canPublish && pending ? t.pending : "";
  }
  function versionsDialog() {
    var ul = el("ul", { class: "stu-versions" });
    if (!S.versions.length) ul.appendChild(el("li", { text: t.none }));
    S.versions.forEach(function (v) {
      var row = el("div", { class: "stu-row" }, [el("strong", { text: "#" + v.number }), el("span", { class: "stu-badge", "data-state": v.state, text: v.state_label })]);
      if (v.is_live) row.appendChild(el("span", { class: "stu-badge", "data-live": "true", text: t.live }));
      if (v.is_auto) row.appendChild(el("span", { class: "stu-badge", text: "auto" }));
      var meta = el("div", { class: "stu-help", text: [v.created_by, v.created_at, v.note, v.review_note ? "↳ " + v.review_note : ""].filter(Boolean).join(" · ") });
      var acts = el("div", { class: "stu-row" });
      var act = function (label, action, cls) { acts.appendChild(el("button", { type: "button", class: "stu-btn " + (cls || ""), text: label, onclick: function () { closeDialog(); versionAction(v.number, action); } })); };
      act(t.restore, "restore");
      acts.appendChild(el("a", { class: "stu-btn", href: urls.preview + "?version=" + v.number, target: "_blank", rel: "noopener", text: t.viewSite.split(" ")[0] }));
      if (S.canPublish && v.state === "pending") { act(t.approve, "approve", "stu-primary"); act(t.reject, "reject"); }
      if (S.canPublish && !v.is_live && v.state === "approved") act(t.rollback, "rollback");
      ul.appendChild(el("li", {}, [row, meta, acts]));
    });
    var buttons = [];
    if (S.canPublish && S.live !== null) buttons.push([t.unpublish, function () { postVersion("unpublish"); }, ""]);
    openDialog(t.versions, ul, buttons);
  }
  function versionAction(number, action) {
    var go = function (note) {
      flush().then(function () { return api(urls.versionBase + number + "/", "POST", { action: action, note: note || "" }); }).then(function (r) {
        if (!r.ok) { status(r.error || t.saveFailed, "error"); if (r.errors) showErrors(r.errors); return; }
        applyState(r, action === "restore" || action === "rollback");
        status(t.saved, "saved");
      });
    };
    if (action === "reject" || action === "approve") {
      var inp = el("input", { type: "text", maxlength: 200, placeholder: t.noteOptional, "aria-label": t.noteOptional });
      inp.style.cssText = "inline-size:100%;padding:.45rem .6rem";
      openDialog(action === "reject" ? t.reject : t.approve, inp, [[action === "reject" ? t.reject : t.approve, function () { go(inp.value); }, "stu-primary"]]);
    } else go("");
  }
  function applyState(r, reloadDoc) {
    S.versions = r.versions || S.versions; S.live = r.live; S.canPublish = r.canPublish;
    if (r.revision !== undefined && (reloadDoc || !S.dirty)) {
      if (reloadDoc || r.revision !== S.rev) { S.rev = r.revision; if (reloadDoc && r.document) { S.doc = r.document; S.history = new C.History(100); S.selected = C.find(S.doc, S.selected) ? S.selected : ""; S.dirty = false; renderTree(); renderInspector(); schedulePreview(); updateUndoButtons(); } }
    }
    refreshPublishButton();
  }

  // ================================================================== import / export
  function exportFile(sectionId) {
    flush().then(function () { window.location.href = urls.export + (sectionId ? "?section=" + encodeURIComponent(sectionId) : ""); });
  }
  function ioDialog() {
    var file = el("input", { type: "file", accept: "application/json,.json", "aria-label": t.importFile });
    var mode = el("select", { "aria-label": t.importFile }, [el("option", { value: "append", text: t.importAppend }), el("option", { value: "replace", text: t.importReplace })]);
    var msg = el("p", { class: "stu-help", role: "status" });
    var body = el("div", {}, [el("p", {}, [el("button", { type: "button", class: "stu-btn", text: t.exportPage, onclick: function () { closeDialog(); exportFile(""); } })]), el("hr"), el("p", {}, [file]), el("p", {}, [mode]), msg]);
    openDialog(t.importFile + " / " + t.exportPage, body, [[t.importFile, function () {
      var fl = file.files[0];
      if (!fl) return;
      var reader = new FileReader();
      reader.onload = function () {
        var payload;
        try { payload = JSON.parse(String(reader.result)); } catch (x) { return status(t.invalid, "error"); }
        flush().then(function () { return api(urls.import, "POST", { payload: payload, mode: mode.value }); }).then(function (r) {
          if (!r.ok) { status(r.error || t.invalid, "error"); if (r.errors) showErrors(r.errors); return; }
          applyState(r, true); status(t.importDone, "saved"); announce(t.importDone);
          if (r.clearedMedia) banner("imported", "info", fmt(t.importCleared, r.clearedMedia), [[t.close, function () { banner("imported", "info", null); }]]);
        });
      };
      reader.readAsText(fl);
    }, "stu-primary"]]);
  }

  // ================================================================== keyboard
  function inField(target) { return target && target.closest && target.closest("input,textarea,select,[contenteditable]"); }
  function handleKey(e) {
    var k = (e.key || "").toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && k === "s") { e.preventDefault(); saveDraft(); return; }
    if (inField(e.target) || $("stu-dialog").open) return;
    if (mod && k === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
    else if (mod && k === "y") { e.preventDefault(); redo(); }
    else if (mod && k === "d" && S.selected) { e.preventDefault(); duplicateNode(S.selected); }
    else if ((k === "delete") && S.selected) { e.preventDefault(); deleteNode(S.selected, true); }
  }
  document.addEventListener("keydown", handleKey);
  window.addEventListener("beforeunload", function (e) { if (S.dirty && !S.conflict) { e.preventDefault(); e.returnValue = ""; } });

  // ================================================================== boot
  function loadState(force) {
    return api(urls.state).then(function (r) {
      if (!r.ok) { status(r.error || t.saveFailed, "error"); return; }
      S.doc = r.document; S.rev = r.revision; S.versions = r.versions; S.live = r.live; S.canPublish = r.canPublish; S.dirty = false;
      S.history = new C.History(100); S.otherEditor = r.otherEditor; S.conflict = false;
      if (S.selected && !C.find(S.doc, S.selected)) S.selected = "";
      banner("other", "warn", r.otherEditor ? fmt(t.otherEditor, r.otherEditor.name) : null);
      refreshPublishButton(); updateUndoButtons(); renderTree(); renderInspector();
      status(t.saved, "saved");
      if (force) renderPreview();
      $("stu").setAttribute("data-state", "ready");
      S.loaded = true;
    });
  }
  function bind() {
    $("stu-undo").addEventListener("click", undo);
    $("stu-redo").addEventListener("click", redo);
    $("stu-io").addEventListener("click", ioDialog);
    $("stu-versions").addEventListener("click", versionsDialog);
    $("stu-save-version").addEventListener("click", function () { noteDialog(t.saveVersion, "", "save", t.save); });
    $("stu-publish").addEventListener("click", function () {
      if (S.canPublish) noteDialog(t.publish, "", "publish", t.publish); else noteDialog(t.submit, "", "submit", t.submit);
    });
    Array.prototype.forEach.call(document.querySelectorAll("#stu-devices [data-device]"), function (b) { b.addEventListener("click", function () { setDevice(b.getAttribute("data-device")); }); });
    var tabs = Array.prototype.slice.call(document.querySelectorAll("#stu-left-tabs [data-tab]"));
    tabs.forEach(function (b) {
      b.addEventListener("click", function () { setLeftTab(b.getAttribute("data-tab")); });
      b.addEventListener("keydown", function (e) {
        var i = tabs.indexOf(b), n = e.key === "ArrowRight" || e.key === "ArrowLeft" ? (e.key === (rtl ? "ArrowLeft" : "ArrowRight") ? 1 : -1) : 0;
        if (n) { e.preventDefault(); var to = tabs[(i + n + tabs.length) % tabs.length]; to.focus(); setLeftTab(to.getAttribute("data-tab")); }
      });
    });
    $("stu-classic").addEventListener("click", function (e) {
      if (S.dirty) { e.preventDefault(); flush().then(function () { window.location.href = urls.classicEdit; }); }
    });
    $("stu-back").addEventListener("click", function (e) { if (S.dirty) { e.preventDefault(); flush().then(function () { window.location.href = urls.list; }); } });
  }
  function setLeftTab(name) {
    S.leftTab = name;
    ["widgets", "layers"].forEach(function (n) {
      $("panel-" + n).hidden = n !== name;
      var b = $("tab-" + n); b.setAttribute("aria-selected", n === name ? "true" : "false"); b.tabIndex = n === name ? 0 : -1;
    });
  }

  function boot() {
    window.__studioIcons = Object.assign({
      grip: '<circle cx="9" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="18" r="1"/>',
      puzzle: '<path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z"/>'
    }, cfg.iconPaths || {});
    buildPalette(); bind(); setLeftTab("widgets");
    frame.src = urls.preview;
    loadState(false).then(function () { clearInterval(timers.ping); timers.ping = setInterval(ping, 30000); });
    if (cfg.targetUrl) { /* link back to the live page is offered from the versions dialog */ }
  }
  boot();
})();
