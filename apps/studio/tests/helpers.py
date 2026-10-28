import copy
from unittest import skipUnless

from django.conf import settings

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: F401

from apps.core.testing import MediaTestCase, png_bytes
from apps.media_library.models import Media
from apps.studio.fields import field_defaults
from apps.studio.registry import get_widget
from apps.studio.schema import COLUMN_PROPS, SECTION_PROPS, empty_document

# Tests that open the editor or its API need the module; the "no-studio" CI run (RAVIX_STUDIO_ENABLED=false) skips them and keeps the rest.
requires_studio = skipUnless(settings.STUDIO_ENABLED, "needs the Ravix Studio module (RAVIX_STUDIO_ENABLED)")

_n = [0]


def nid():
    _n[0] += 1
    return f"t{_n[0]:05d}"


def widget(type_, style=None, **props):
    spec = get_widget(type_)
    p = field_defaults(spec.fields)
    p.update(props)
    node = {"id": nid(), "type": type_, "props": p, "style": style or {}}
    if spec.container:
        node["columns"] = []
    return node


def column(*widgets, style=None, **props):
    p = field_defaults(COLUMN_PROPS)
    p.update(props)
    return {"id": nid(), "type": "column", "props": p, "style": style or {}, "widgets": list(widgets)}


def section(*columns, style=None, **props):
    p = field_defaults(SECTION_PROPS)
    p.update(props)
    return {"id": nid(), "type": "section", "props": p, "style": style or {}, "columns": list(columns) or [column()]}


def document(*sections, **meta):
    d = empty_document()
    d["meta"].update(meta)
    d["sections"] = list(sections)
    return d


def simple_doc(text="سلام دنیا", **meta):
    return document(section(column(widget("heading", text=text))), **meta)


def clone(d):
    return copy.deepcopy(d)


class StudioTestCase(MediaTestCase):
    def make_image(self, name="a.png", size=(40, 30)):
        m = Media(original_name=name, kind=Media.Kind.IMAGE, mime_type="image/png", width=size[0], height=size[1], alt_text="library alt")
        m.file.save(name, ContentFile(png_bytes(size)), save=True)
        return m


# ------------------------------------------------------------------------------------------------ shared fixtures
from django.test import override_settings  # noqa: E402

from apps.accounts.permissions import Role  # noqa: E402
from apps.accounts.tests.test_auth import make_user  # noqa: E402
from apps.content.models import Page, Post  # noqa: E402


class StudioBase(StudioTestCase):
    """Users of every role, one page and one draft post, and a ready draft helper."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.admin = make_user("boss", Role.ADMIN)
        self.root = make_user("root", Role.SUPER_ADMIN)
        self.author = make_user("writer", Role.AUTHOR)
        self.other_author = make_user("writer2", Role.AUTHOR)
        self.visitor = make_user("visitor", Role.USER)
        self.page = Page.objects.create(title="About", slug="about", body="old text", status="published", author=self.admin, language="en")
        self.post = Post.objects.create(title="Mine", slug="mine", body="x", status="draft", author=self.author, language="en")

    def document_for(self, target=None):
        from apps.studio import services

        return services.get_document(target or self.page)

    def put_draft(self, doc, target=None, user=None):
        """Save ``doc`` as the draft of the page (default) through the real service."""
        from apps.studio import services

        d = self.document_for(target)
        services.save_draft(d.pk, doc, d.draft_rev, user or self.admin)
        d.refresh_from_db()
        return d
