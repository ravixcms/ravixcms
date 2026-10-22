import copy

from django.core.files.base import ContentFile

from apps.builder.schema import defaults, empty_document
from apps.core.testing import MediaTestCase, png_bytes
from apps.media_library.models import Media


def node_ids():
    n = [0]

    def nxt():
        n[0] += 1
        return f"t{n[0]:04d}"
    return nxt


_next = node_ids()


def el(type_, **props):
    p = defaults(type_)
    p.update(props)
    return {"id": _next(), "type": type_, "props": p}


def col(*elements, **props):
    p = defaults("column")
    p.update(props)
    return {"id": _next(), "props": p, "elements": list(elements)}


def row(*columns, **props):
    p = defaults("row")
    p.update(props)
    return {"id": _next(), "props": p, "columns": list(columns)}


def section(*rows, **props):
    p = defaults("section")
    p.update(props)
    return {"id": _next(), "props": p, "rows": list(rows)}


def document(*sections, **meta):
    d = empty_document()
    d["meta"].update(meta)
    d["sections"] = list(sections)
    return d


def simple_doc(text="سلام دنیا", **meta):
    return document(section(row(col(el("heading", text=text), span=12))), **meta)


def clone(d):
    return copy.deepcopy(d)


class BuilderTestCase(MediaTestCase):
    def make_image(self, name="a.png"):
        m = Media(original_name=name, kind=Media.Kind.IMAGE, mime_type="image/png", width=40, height=30, alt_text="متن جایگزین کتابخانه")
        m.file.save(name, ContentFile(png_bytes((40, 30))), save=True)
        return m
