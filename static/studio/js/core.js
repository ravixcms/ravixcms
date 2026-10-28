/* Ravix Studio — the document model of the editor (no DOM here, so it can be tested with Node).
   A document is the tree the server validates: sections → columns → widgets (an inner section holds columns again). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.StudioCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  var DEVICES = ["desktop", "tablet", "mobile"];

  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function newId() {
    var a = new Uint8Array(4);
    (self.crypto || window.crypto).getRandomValues(a);
    var s = "n";
    for (var i = 0; i < a.length; i++) s += ("0" + a[i].toString(16)).slice(-2);
    return s;
  }
  function defaultsOf(fields) {
    var out = {};
    (fields || []).forEach(function (f) { out[f.name] = clone(f.default); });
    return out;
  }

  // ------------------------------------------------------------------ lookup
  /* Every list a node can live in: [{owner, kind, items}]; kind is the type of the nodes inside. */
  function listOf(doc, ownerId) {
    if (ownerId === "root") return { items: doc.sections, kind: "section", owner: null };
    var f = find(doc, ownerId);
    if (!f) return null;
    var n = f.node;
    if (f.kind === "section" || (f.kind === "widget" && n.columns)) return { items: n.columns, kind: "column", owner: n };
    if (f.kind === "column") return { items: n.widgets, kind: "widget", owner: n };
    return null;
  }

  function find(doc, id) {
    var found = null;
    (function walk(items, kind, parentId, depth) {
      for (var i = 0; i < items.length && !found; i++) {
        var n = items[i];
        if (n.id === id) { found = { node: n, kind: kind, parentId: parentId, index: i, list: items, depth: depth }; return; }
        if (kind === "section") walk(n.columns, "column", n.id, depth);
        else if (kind === "column") walk(n.widgets, "widget", n.id, depth);
        else if (kind === "widget" && n.columns) walk(n.columns, "column", n.id, depth + 1);
      }
    })(doc.sections, "section", "root", 1);
    return found;
  }

  function each(doc, fn) {
    (function walk(items, kind, parent, depth) {
      items.forEach(function (n, i) {
        fn(n, kind, parent, i, depth);
        if (kind === "section") walk(n.columns, "column", n, depth);
        else if (kind === "column") walk(n.widgets, "widget", n, depth);
        else if (n.columns) walk(n.columns, "column", n, depth + 1);
      });
    })(doc.sections, "section", null, 1);
  }

  function count(node, kind) {
    var c = 1;
    if (kind === "section" || (node.columns)) node.columns.forEach(function (col) { c += count(col, "column"); });
    else if (kind === "column") node.widgets.forEach(function (w) { c += count(w, "widget"); });
    return c;
  }
  function total(doc) { var c = 0; each(doc, function () { c++; }); return c; }

  /* How many levels of inner sections a node needs: 0 for a plain widget, 1 for an empty inner section, and so on. */
  function levels(node, kind) {
    var best = 0;
    if (kind === "section" || kind === "widget") {
      (node.columns || []).forEach(function (c) { best = Math.max(best, levels(c, "column")); });
      return kind === "widget" && node.columns ? best + 1 : best;
    }
    (node.widgets || []).forEach(function (w) { best = Math.max(best, levels(w, "widget")); });
    return best;
  }
  /* The level (1 = top) at which the nodes of a list sit. */
  function listLevel(doc, ownerId) {
    if (ownerId === "root") return 1;
    var f = find(doc, ownerId);
    if (!f) return 1;
    return f.kind === "widget" ? f.depth + 1 : f.depth;
  }

  // ------------------------------------------------------------------ builders
  function makeWidget(spec) {
    var w = { id: newId(), type: spec.type, props: defaultsOf(spec.fields), style: clone(spec.default_style || {}) };
    if (spec.container) w.columns = [makeColumn(spec.specs), makeColumn(spec.specs)];
    return w;
  }
  function makeColumn(specs) {
    var c = { id: newId(), type: "column", props: defaultsOf(specs.column.fields), style: {}, widgets: [] };
    return c;
  }
  function makeSection(specs, spans) {
    var cols = (spans || [12]).map(function (span) {
      var c = makeColumn(specs);
      if (span !== 12 || (spans || []).length > 1) c.style = { desktop: { span: span } };
      return c;
    });
    return { id: newId(), type: "section", props: defaultsOf(specs.section.fields), style: {}, columns: cols };
  }
  function emptyDocument(specs) {
    var meta = {};
    (specs.meta || []).forEach(function (f) { meta[f.name] = f.default; });
    return { schema_version: specs.schema_version, meta: meta, sections: [] };
  }

  // ------------------------------------------------------------------ checks (the server re-checks everything)
  function canInsert(doc, specs, ownerId, node, kind, ignoreId) {
    var L = listOf(doc, ownerId), lim = specs.limits;
    if (!L || L.kind !== kind) return "kind";
    var moving = function () { return ignoreId && L.items.some(function (n) { return n.id === ignoreId; }); };
    if (ownerId === "root" && L.items.length >= lim.sections && !moving()) return "sections";
    if (kind === "column" && L.items.length >= lim.columns_per_section && !moving()) return "columns";
    if (kind === "widget" && L.items.length >= lim.widgets_per_column && !moving()) return "widgets";
    if (listLevel(doc, ownerId) + levels(node, kind) > lim.max_depth) return "depth";
    if (!ignoreId && total(doc) + count(node, kind) > lim.total_nodes) return "nodes";
    return "";
  }

  // ------------------------------------------------------------------ operations (mutate doc, return true on success)
  function insert(doc, specs, ownerId, index, node, kind) {
    var why = canInsert(doc, specs, ownerId, node, kind);
    if (why) return why;
    var L = listOf(doc, ownerId);
    L.items.splice(Math.max(0, Math.min(index, L.items.length)), 0, node);
    return "";
  }
  function remove(doc, id) {
    var f = find(doc, id);
    if (!f) return false;
    f.list.splice(f.index, 1);
    return true;
  }
  function move(doc, specs, id, ownerId, index) {
    var f = find(doc, id);
    if (!f) return "missing";
    if (ownerId === id || (ownerId !== "root" && isInside(f.node, f.kind, ownerId))) return "inside";
    var L = listOf(doc, ownerId);
    if (!L || L.kind !== f.kind) return "kind";
    var why = canInsert(doc, specs, ownerId, f.node, f.kind, id);
    if (why) return why;
    f.list.splice(f.index, 1);
    L = listOf(doc, ownerId);
    L.items.splice(Math.max(0, Math.min(index, L.items.length)), 0, f.node);
    return "";
  }
  function isInside(node, kind, id) {
    var hit = false;
    (function walk(n, k) {
      if (hit) return;
      var kids = k === "section" ? n.columns : k === "column" ? n.widgets : n.columns || [];
      kids.forEach(function (c) { if (c.id === id) hit = true; else walk(c, k === "section" ? "column" : k === "column" ? "widget" : "column"); });
    })(node, kind);
    return hit;
  }
  function reId(node) {
    node.id = newId();
    (node.columns || []).forEach(reId);
    (node.widgets || []).forEach(reId);
    return node;
  }
  function duplicate(doc, specs, id) {
    var f = find(doc, id);
    if (!f) return { error: "missing" };
    var copy = reId(clone(f.node));
    var why = canInsert(doc, specs, f.parentId, copy, f.kind);
    if (why) return { error: why };
    f.list.splice(f.index + 1, 0, copy);
    return { id: copy.id };
  }
  /* Step an item up/down in its list, or (for a widget) into the neighbouring column. dir: -1 | 1. mode: "list" | "column". */
  function step(doc, specs, id, dir, mode) {
    var f = find(doc, id);
    if (!f) return "missing";
    if (mode === "column" && f.kind === "widget") {
      var col = find(doc, f.parentId), sec = col && find(doc, col.parentId);
      if (!sec) return "missing";
      var target = sec.node.columns[col.index + dir];
      if (!target) return "edge";
      return move(doc, specs, id, target.id, dir < 0 ? target.widgets.length : 0);
    }
    var to = f.index + dir;
    if (to < 0 || to >= f.list.length) return "edge";
    return move(doc, specs, id, f.parentId, to + (dir > 0 ? 1 : 0));
  }

  function setProp(doc, id, name, value) {
    var f = find(doc, id);
    if (!f) return false;
    f.node.props[name] = value;
    return true;
  }
  /* Style is stored per device; an unset value inherits from the larger screen. value === "" / null / undefined removes it. */
  function setStyle(doc, id, device, name, value) {
    var f = find(doc, id);
    if (!f) return false;
    var st = f.node.style = f.node.style || {};
    var d = st[device] = st[device] || {};
    if (value === "" || value === null || value === undefined) delete d[name];
    else d[name] = value;
    if (!Object.keys(d).length) delete st[device];
    return true;
  }
  function styleValue(node, device, name) {  // {value, from} - the value in force for a device and the device it comes from
    var order = DEVICES.slice(0, DEVICES.indexOf(device) + 1).reverse();
    for (var i = 0; i < order.length; i++) {
      var d = (node.style || {})[order[i]];
      if (d && Object.prototype.hasOwnProperty.call(d, name)) return { value: d[name], from: order[i] };
    }
    return { value: undefined, from: "" };
  }

  // ------------------------------------------------------------------ labels for the layers panel
  function label(node, kind, specs, strings) {
    if (kind === "section") return strings.section;
    if (kind === "column") return strings.column;
    var w = (specs.widgets || []).filter(function (x) { return x.type === node.type; })[0];
    var name = w ? w.title : node.type;
    var p = node.props || {};
    var hint = p.text || p.title || p.label || p.content || p.quote || "";
    if (typeof hint === "string" && hint) {
      hint = hint.replace(/[#*_`>\[\]\n]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 28);
      if (hint) return name + " — " + hint;
    }
    return name;
  }

  // ------------------------------------------------------------------ undo / redo
  function History(limit) {
    this.limit = limit || 100;
    this.past = [];
    this.future = [];
    this.lastKey = "";
    this.lastAt = 0;
  }
  History.prototype.push = function (snapshot, key) {  // snapshot = state BEFORE the change; same key within 800 ms merges (typing)
    var now = Date.now();
    if (key && key === this.lastKey && now - this.lastAt < 800 && this.past.length) { this.lastAt = now; this.future = []; return; }
    this.past.push(snapshot);
    if (this.past.length > this.limit) this.past.shift();
    this.future = [];
    this.lastKey = key || "";
    this.lastAt = now;
  };
  History.prototype.undo = function (current) {
    if (!this.past.length) return null;
    this.future.push(current);
    this.lastKey = "";
    return this.past.pop();
  };
  History.prototype.redo = function (current) {
    if (!this.future.length) return null;
    this.past.push(current);
    this.lastKey = "";
    return this.future.pop();
  };
  History.prototype.canUndo = function () { return this.past.length > 0; };
  History.prototype.canRedo = function () { return this.future.length > 0; };

  return {
    DEVICES: DEVICES, clone: clone, newId: newId, defaultsOf: defaultsOf, find: find, each: each, listOf: listOf, total: total,
    makeWidget: makeWidget, makeColumn: makeColumn, makeSection: makeSection, emptyDocument: emptyDocument,
    canInsert: canInsert, insert: insert, remove: remove, move: move, duplicate: duplicate, step: step, reId: reId,
    setProp: setProp, setStyle: setStyle, styleValue: styleValue, label: label, History: History
  };
});
