/* Ravix Studio — builds the setting controls of the inspector from the field specs the server sends.
   One function per field kind; every control reports through o.onChange(value) and is a plain, labelled, keyboard-friendly form control. */
(function (root) {
  "use strict";
  var uid = 0;

  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v === null || v === undefined) return;
      if (k === "text") node.textContent = v;
      else if (k === "class") node.className = v;
      else if (k.slice(0, 2) === "on") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach(function (c) { if (c) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }

  var HEX = /^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
  function colorForInput(v) {
    if (!HEX.test(v || "")) return "#000000";
    if (v.length === 4 || v.length === 5) return "#" + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
    return v.slice(0, 7);
  }

  /* o: {value, placeholder, allowEmpty, canHtml, t, icons, onChange, pickMedia(kind, done), mediaUrl(id, done), explicit} */
  function control(f, o, id) {
    var t = o.t, v = o.value, k = f.kind, box;
    var emit = function (x) { o.onChange(x); };
    var common = { id: id, "aria-describedby": f.help ? id + "-help" : null };
    if (k === "text" || k === "anchor" || k === "url" || k === "video") {
      return el("input", Object.assign({ type: "text", value: v == null ? "" : v, maxlength: f.max_len || 200, placeholder: o.placeholder || "",
        dir: k === "text" ? null : "ltr", inputmode: k === "url" ? "url" : null, autocomplete: "off", oninput: function (e) { emit(e.target.value); } }, common));
    }
    if (k === "textarea" || k === "markdown" || k === "html") {
      var ro = k === "html" && !o.canHtml;
      var area = el("textarea", Object.assign({ maxlength: f.max_len || 5000, readonly: ro, dir: k === "html" ? "ltr" : null, rows: k === "html" ? 8 : 5,
        placeholder: o.placeholder || "", oninput: function (e) { emit(e.target.value); } }, common));
      area.value = v == null ? "" : v;
      if (ro) area.title = t.adminOnly;
      return area;
    }
    if (k === "int") {
      return el("input", Object.assign({ type: "number", step: 1, min: f.min, max: f.max, value: v === undefined || v === null ? "" : v, placeholder: o.placeholder !== undefined ? o.placeholder : "",
        oninput: function (e) {
          var s = e.target.value;
          if (s === "") return emit(o.allowEmpty ? "" : f.default);
          var n = parseInt(s, 10);
          if (!isNaN(n)) emit(Math.max(f.min != null ? f.min : -1e9, Math.min(f.max != null ? f.max : 1e9, n)));
        } }, common));
    }
    if (k === "choice") {
      var sel = el("select", Object.assign({ onchange: function (e) { emit(e.target.value); } }, common));
      var has = false;
      (f.choices || []).forEach(function (c) { if (c[0] === "") has = true; });
      if (o.allowEmpty && !has) sel.appendChild(el("option", { value: "", text: o.placeholder ? "— " + o.placeholder : "—" }));
      (f.choices || []).forEach(function (c) { sel.appendChild(el("option", { value: c[0], text: c[1] })); });
      sel.value = v == null ? "" : String(v);
      return sel;
    }
    if (k === "bool") {
      if (o.allowEmpty) {
        var s2 = el("select", Object.assign({ onchange: function (e) { emit(e.target.value === "" ? "" : e.target.value === "1"); } }, common),
          [el("option", { value: "", text: o.placeholder ? "— " + o.placeholder : "—" }), el("option", { value: "1", text: t.yes }), el("option", { value: "0", text: t.no })]);
        s2.value = v === true ? "1" : v === false ? "0" : "";
        return s2;
      }
      return el("input", Object.assign({ type: "checkbox", checked: !!v, onchange: function (e) { emit(e.target.checked); } }, common));
    }
    if (k === "color") {
      var hint = HEX.test(o.placeholder || "") ? o.placeholder : "";
      var text = el("input", { type: "text", dir: "ltr", value: v || "", placeholder: hint || "#RRGGBB", maxlength: 9, id: id, autocomplete: "off",
        "aria-describedby": common["aria-describedby"], oninput: function (e) { var x = e.target.value.trim(); if (x === "" || HEX.test(x)) { swatch.value = colorForInput(x || hint); emit(x); } } });
      var swatch = el("input", { type: "color", value: colorForInput(v || hint), "aria-label": f.label, oninput: function (e) { text.value = e.target.value; emit(e.target.value); } });
      box = el("div", { class: "stu-inline" }, [swatch, text]);
      return box;
    }
    if (k === "icon") {
      var isel = el("select", Object.assign({ onchange: function (e) { emit(e.target.value); } }, common));
      if (o.allowEmpty || !f.required) isel.appendChild(el("option", { value: "", text: t.none }));
      (o.icons || []).forEach(function (n) { isel.appendChild(el("option", { value: n, text: n })); });
      isel.value = v || "";
      return isel;
    }
    if (k === "media") {
      var img = el("img", { alt: "", width: 54, height: 54 });
      var info = el("span", { class: "stu-help", text: "" });
      var show = function (val) {
        if (!val) { img.removeAttribute("src"); img.style.visibility = "hidden"; return; }
        img.style.visibility = "visible";
        o.mediaUrl(val, function (u) { if (u) img.src = u; });
      };
      show(v);
      var choose = el("button", { type: "button", class: "stu-btn", text: v ? t.change : (f.media_kind === "video" ? t.chooseVideo : t.chooseImage), id: id,
        onclick: function () { o.pickMedia(f.media_kind, function (m) { show(m.id); choose.textContent = t.change; emit(m.id); }); } });
      var clear = el("button", { type: "button", class: "stu-btn", text: t.remove, onclick: function () { show(null); choose.textContent = f.media_kind === "video" ? t.chooseVideo : t.chooseImage; emit(null); } });
      return el("div", { class: "stu-media" }, [img, el("div", { class: "stu-inline" }, [choose, clear]), info]);
    }
    if (k === "list") return listControl(f, o, id);
    return el("span", { text: "?" });
  }

  function listControl(f, o, id) {
    var items = Array.isArray(o.value) ? JSON.parse(JSON.stringify(o.value)) : [];
    var t = o.t;
    var wrap = el("div", { id: id });
    function commit() { o.onChange(JSON.parse(JSON.stringify(items))); }
    function blank() {
      var item = {};
      (f.item_fields || []).forEach(function (sub) { item[sub.name] = JSON.parse(JSON.stringify(sub.default)); });
      return item;
    }
    function paint() {
      wrap.textContent = "";
      items.forEach(function (item, i) {
        var title = "";
        (f.item_fields || []).some(function (sub) { if (sub.kind === "text" && item[sub.name]) { title = item[sub.name]; return true; } return false; });
        var det = el("details", { class: "stu-item" });
        var bar = el("summary", {}, [el("span", { text: (t.item + " " + (i + 1)) + (title ? " — " + title : "") })]);
        var btn = function (txt, label, fn, dis) { return el("button", { type: "button", class: "stu-link-btn", text: txt, "aria-label": label, disabled: dis, onclick: function (e) { e.preventDefault(); e.stopPropagation(); fn(); } }); };
        bar.appendChild(btn("↑", t.moveUp, function () { var x = items.splice(i, 1)[0]; items.splice(i - 1, 0, x); commit(); paint(); }, i === 0));
        bar.appendChild(btn("↓", t.moveDown, function () { var x = items.splice(i, 1)[0]; items.splice(i + 1, 0, x); commit(); paint(); }, i === items.length - 1));
        bar.appendChild(btn("✕", t.delete, function () { items.splice(i, 1); commit(); paint(); }));
        var body = el("div");
        (f.item_fields || []).forEach(function (sub) {
          body.appendChild(field(sub, Object.assign({}, o, { value: item[sub.name], allowEmpty: false, placeholder: undefined, onChange: function (val) { item[sub.name] = val; commit(); } })));
        });
        det.appendChild(bar); det.appendChild(body);
        wrap.appendChild(det);
      });
      if (items.length < (f.max_items || 20)) wrap.appendChild(el("button", { type: "button", class: "stu-btn", text: "+ " + t.addItem, onclick: function () { items.push(blank()); commit(); paint(); var all = wrap.querySelectorAll("details"); if (all.length) all[all.length - 1].open = true; } }));
    }
    paint();
    return wrap;
  }

  /* A labelled field. o.explicit (style fields) tells if this device has its own value; o.from names the device an inherited value comes from. */
  function field(f, o) {
    var id = "stf-" + (++uid);
    var box = el("div", { class: "stu-field", "data-kind": f.kind, "data-inherited": o.from ? "true" : null });
    var lab = el("label", { for: id }, [f.label]);
    if (f.kind === "list") lab = el("span", { class: "stu-label", text: f.label });
    if (o.from) lab.appendChild(el("span", { class: "stu-from", text: o.from }));
    if (o.canReset && o.explicit) lab.appendChild(el("button", { type: "button", class: "stu-link-btn", text: o.t.reset, "aria-label": o.t.reset + ": " + f.label, onclick: function () { o.onChange(""); if (o.rebuild) o.rebuild(); } }));
    box.appendChild(lab);
    if (f.kind === "bool" && !o.allowEmpty) {
      var row = el("label", { class: "stu-inline" }, [control(f, o, id), el("span", { text: f.label })]);
      box.textContent = "";
      box.appendChild(row);
    } else {
      box.appendChild(control(f, o, id));
    }
    if (f.help) box.appendChild(el("div", { class: "stu-help", id: id + "-help", text: f.help }));
    return box;
  }

  root.StudioInspector = { field: field, el: el };
})(typeof self !== "undefined" ? self : this);
