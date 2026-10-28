"""
Frozen converter of the first page builder's documents (schema 1: section → row → column → element) to Ravix Studio's schema 2
(section → column → widget). Used by the data migration ``studio.0002`` and by ``schema.migrate_document``. Do not change its behaviour:
a migration depends on it. It has no Django dependencies on purpose.
"""
import hashlib
import re


def _derived_id(parent: str, tag: str) -> str:
    return "n" + hashlib.sha1(f"{parent}:{tag}".encode(), usedforsecurity=False).hexdigest()[:8]


def _escape_md(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>()#+\-.!|~])", r"\\\1", text)


def v1_to_v2(doc: dict) -> dict:
    """The first page builder (section → row → column → element) becomes section → column → widget."""
    def box(props, margin_bottom=True):
        style = {}
        if props.get("bg_color"):
            style["bg_color"] = props["bg_color"]
        if props.get("padding"):
            style.update(padding_top=props["padding"], padding_bottom=props["padding"], padding_start=props["padding"], padding_end=props["padding"])
        if props.get("border_width"):
            style.update(border_width=props["border_width"], border_style=props.get("border_style", "solid"))
            if props.get("border_color"):
                style["border_color"] = props["border_color"]
        if props.get("radius"):
            style["radius"] = props["radius"]
        if margin_bottom and props.get("margin_bottom"):
            style["margin_bottom"] = props["margin_bottom"]
        return style

    def widget(el):
        p, t = el.get("props", {}), el.get("type")
        style = box(p)
        if p.get("align"):
            style["text_align"] = p["align"]
        if t == "heading":
            if p.get("color"):
                style["color"] = p["color"]
            if p.get("font_size"):
                style["font_size"] = p["font_size"]
            props = {"text": p.get("text", ""), "tag": f"h{p.get('level', '2')}"}
        elif t == "text":
            if p.get("color"):
                style["color"] = p["color"]
            if p.get("font_size"):
                style["font_size"] = p["font_size"]
            props = {"content": _escape_md(p.get("text", ""))}
        elif t == "image":
            props = {"media": p.get("media"), "alt": p.get("alt", ""), "link_url": p.get("link_url", ""), "link_new_tab": bool(p.get("link_new_tab"))}
            if p.get("width_pct") and p["width_pct"] != 100:
                style["width"] = p["width_pct"]
        else:  # button
            props = {"label": p.get("label", ""), "url": p.get("url", "/"), "new_tab": bool(p.get("new_tab")), "variant": p.get("variant", "solid")}
            for old, new in (("bg_color", "btn_color"), ("text_color", "btn_text"), ("font_size", "font_size"), ("radius", "btn_radius")):
                if p.get(old) not in (None, ""):
                    style[new] = p[old]
            style.pop("bg_color", None)
            style.pop("radius", None) if "btn_radius" in style else None
        return {"id": el["id"], "type": t, "props": props, "style": {"desktop": style} if style else {}}

    def columns(row):
        rp = row.get("props", {})
        stack = rp.get("stack_at", "mobile")
        out = []
        for col in row.get("columns", []):
            cp = col.get("props", {})
            desktop = box(cp, margin_bottom=False)
            desktop["span"] = cp.get("span", 12)
            style = {"desktop": desktop}
            if stack == "tablet":
                style["tablet"] = {"span": 12}
            if stack in ("tablet", "mobile"):
                style["mobile"] = {"span": 12}
            out.append({"id": col["id"], "type": "column", "props": {}, "style": style, "widgets": [widget(e) for e in col.get("elements", [])]})
        return out

    sections = []
    for sec in doc.get("sections", []):
        sp = sec.get("props", {})
        style = {}
        if sp.get("padding_y"):
            style.update(padding_top=sp["padding_y"], padding_bottom=sp["padding_y"])
        if sp.get("bg_color"):
            style["bg_color"] = sp["bg_color"]
        if sp.get("bg_media"):
            style["bg_media"] = sp["bg_media"]
        if sp.get("text_color"):
            style["color"] = sp["text_color"]
        if sp.get("border_width"):
            style.update(border_width=sp["border_width"], border_style=sp.get("border_style", "solid"))
            if sp.get("border_color"):
                style["border_color"] = sp["border_color"]
        if sp.get("radius"):
            style["radius"] = sp["radius"]
        rows = sec.get("rows", [])
        props = {"content_width": sp.get("width", "boxed")}
        if len(rows) == 1:
            rp = rows[0].get("props", {})
            props.update(gap=rp.get("gap", 24), valign=rp.get("valign", "start"))
            cols = columns(rows[0])
        elif rows:
            inner = []
            for row in rows:
                rp = row.get("props", {})
                inner.append({"id": row["id"], "type": "inner_section", "props": {"gap": rp.get("gap", 24), "valign": rp.get("valign", "start")},
                              "style": {}, "columns": columns(row)})
            cols = [{"id": _derived_id(sec["id"], "col"), "type": "column", "props": {}, "style": {"desktop": {"span": 12}}, "widgets": inner}]
        else:
            cols = [{"id": _derived_id(sec["id"], "col"), "type": "column", "props": {}, "style": {"desktop": {"span": 12}}, "widgets": []}]
        sections.append({"id": sec["id"], "type": "section", "props": props, "style": {"desktop": style} if style else {}, "columns": cols})
    return {"schema_version": 2, "meta": {"template": "full_width", "show_title": bool(doc.get("meta", {}).get("show_title", True))}, "sections": sections}


