import json

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.templatetags.static import static
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import gettext
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.content.models import Page, Post
from apps.core.params import safe_id, safe_ids, safe_int
from apps.core.templatetags.ravix_ui import jalali_datetime
from apps.media_library.models import Media
from apps.public import builders as pub
from apps.public.rendering import render_page
from apps.public.views import _crumbs

from . import services
from .fields import DocumentInvalid, icon_data
from .models import StudioDocument, StudioVersion
from .render import render_document
from .schema import LIMITS, client_specs, has_admin_widgets, validate_document


# ============================================================ panel pages
class StudioListView(CapabilityRequiredMixin, TemplateView):
    required_capability = "studio.edit"
    template_name = "studio/list.html"
    PER_PAGE = 20

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        kind = self.request.GET.get("kind", "page")
        kind = kind if kind in ("page", "post") else "page"
        q = self.request.GET.get("q", "").strip()[:100]
        only_pending = self.request.GET.get("pending") == "1"
        if kind == "page":
            qs = Page.objects.select_related("studio", "studio__published_version")
        else:
            qs = Post.objects.select_related("studio", "studio__published_version")
            if not user.has_cap("content.publish"):
                qs = qs.filter(author=user)  # authors design their own posts only
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q))
        if only_pending:
            qs = qs.filter(studio__versions__state=StudioVersion.State.PENDING).distinct()
        qs = qs.order_by("-updated_at")
        total = qs.count()
        page_no = max(1, safe_int(self.request.GET.get("page"), 1, hi=10**6))
        rows = []
        for obj in list(qs[(page_no - 1) * self.PER_PAGE: page_no * self.PER_PAGE]):
            doc = getattr(obj, "studio", None) if hasattr(obj, "studio") else None
            rows.append({
                "obj": obj, "doc": doc, "live": bool(doc and doc.published_version_id),
                "pending": StudioVersion.objects.filter(document=doc, state=StudioVersion.State.PENDING).exists() if doc else False,
            })
        ctx.update(rows=rows, kind=kind, q=q, only_pending=only_pending, total=total, page_no=page_no,
                   has_next=page_no * self.PER_PAGE < total, has_prev=page_no > 1, can_publish=user.has_cap("studio.publish"))
        return ctx


def _editor_document(request, kind, pk) -> StudioDocument:
    try:
        target = services.target_of(kind, pk)
    except (Page.DoesNotExist, Post.DoesNotExist):
        raise Http404
    document = services.get_document(target)
    services.require_edit(request.user, document)
    return document


class EditorView(CapabilityRequiredMixin, TemplateView):
    required_capability = "studio.edit"
    template_name = "studio/editor.html"

    def get_context_data(self, **kwargs):
        document = _editor_document(self.request, kwargs["kind"], kwargs["pk"])
        target = document.target
        user = self.request.user
        d = lambda name, *a: reverse(f"studio:{name}", args=[document.pk, *a])  # noqa: E731
        edit_url = reverse("content:page_update" if document.page_id else "content:post_update", args=[target.pk])
        config = {
            "documentId": document.pk, "kind": document.kind, "title": target.title, "csrf": get_token(self.request),
            "canPublish": user.has_cap("studio.publish"), "canHtml": user.has_cap("studio.html"),
            "specs": client_specs(), "iconPaths": icon_data(),
            "urls": {
                "state": d("api_state"), "draft": d("api_draft"), "render": d("api_render"), "versions": d("api_versions"),
                "versionBase": reverse("studio:api_version", args=[document.pk, 0]).rsplit("0/", 1)[0],
                "preview": d("preview"), "export": d("api_export"), "import": d("api_import"), "ping": d("api_ping"),
                "media": reverse("studio:api_media"), "list": reverse("studio:list"), "classicEdit": edit_url,
            },
            "live": bool(target.is_live) if hasattr(target, "is_live") else False,
            "targetUrl": pub.page_url(target) if document.page_id else pub.post_url(target),
            "static": {"sortable": static("studio/vendor/sortable.min.js")},
            "i18n": editor_messages(),
        }
        return {"target": target, "document": document, "config": config}


class PreviewView(CapabilityRequiredMixin, View):
    """The real page in the active theme (for the editor's iframe), with the draft - or version ``?version=N`` - drawn inside it."""

    required_capability = "studio.edit"
    http_method_names = ["get"]

    def get(self, request, doc_id):
        document = get_object_or_404(StudioDocument, pk=doc_id)
        services.require_edit(request.user, document)
        number = safe_id(request.GET.get("version", ""))
        content = get_object_or_404(StudioVersion, document=document, number=number).content if number is not None else document.draft
        result = render_document(content, preview=True, make_files=False, can_html=True)
        target = document.target
        bridge = (f'<link rel="stylesheet" href="{static("studio/preview.css")}">'
                  f'<script src="{static("studio/vendor/sortable.min.js")}"></script><script src="{static("studio/preview.js")}"></script>')
        # `result` comes from our own sanitising renderer.
        body = mark_safe(f'<style id="st-css">{result.css}</style><div id="st-mount">{result.html}</div>{bridge}')  # nosec B308 B703
        meta = {}
        try:
            meta = validate_document(content)["meta"]
        except DocumentInvalid:
            pass
        title = target.title
        if document.page_id:
            item = pub.page_item(target, body_html=body, builder=True, show_title=bool(meta.get("show_title", True)), full_width=meta.get("template") == "full_width")
        else:
            item = pub.page_item(target, body_html=body, builder=True, show_title=bool(meta.get("show_title", True)), full_width=meta.get("template") == "full_width")
        request.csp_frame_hosts = set(result.frame_hosts)
        crumbs = _crumbs((gettext("Home"), "/"), (title, ""))

        def seo(site):
            return {"title": gettext("Preview: %(title)s") % {"title": title}, "robots": "noindex,nofollow", "description": "", "canonical": ""}

        return render_page(request, "page_detail.html", {"page": item, "breadcrumbs": crumbs}, embedded=True, seo_factory=seo)


# ============================================================ JSON API
def _err(message, status=400, **extra):
    return JsonResponse({"ok": False, "error": message, **extra}, status=status)


class ApiView(View):
    required_capability = "studio.edit"

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return _err(gettext("Please sign in first."), 401)
        if not user.has_cap(self.required_capability):
            return _err(gettext("You do not have permission to do this."), 403)
        return super().dispatch(request, *args, **kwargs)

    def json_body(self, limit=None):
        request = self.request
        if request.content_type != "application/json":
            raise ValueError(gettext("The content type must be application/json."))
        if len(request.body) > (limit or LIMITS["max_json_bytes"] + 8192):
            raise ValueError(gettext("The data size exceeds the limit."))
        try:
            data = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError(gettext("Invalid JSON."))
        if not isinstance(data, dict):
            raise ValueError(gettext("The request body must be a JSON object."))
        return data

    def get_document(self, doc_id) -> StudioDocument:
        document = get_object_or_404(StudioDocument.objects.select_related("page", "post", "updated_by", "published_version"), pk=doc_id)
        services.require_edit(self.request.user, document)
        return document


def _version_json(v: StudioVersion, document: StudioDocument) -> dict:
    return {
        "number": v.number, "state": v.state, "state_label": v.get_state_display(), "is_auto": v.is_auto, "note": v.note,
        "created_by": v.created_by.display_name if v.created_by else "—", "created_at": jalali_datetime(v.created_at),
        "reviewed_by": v.reviewed_by.display_name if v.reviewed_by else "", "review_note": v.review_note,
        "is_live": document.published_version_id == v.pk,
    }


def _state_json(document: StudioDocument, user, *, other=None) -> dict:
    versions = list(document.versions.select_related("created_by", "reviewed_by")[:80])
    live = next((v.number for v in versions if v.pk == document.published_version_id), None)
    if live is None and document.published_version_id:
        live = document.published_version.number
    try:
        draft = validate_document(document.draft, can_html=True)
    except DocumentInvalid:
        draft = document.draft
    return {
        "ok": True, "document": draft, "revision": document.draft_rev, "live": live,
        "versions": [_version_json(v, document) for v in versions], "canPublish": user.has_cap("studio.publish"),
        "targetLive": bool(getattr(document.target, "is_live", False)), "otherEditor": other,
        "updatedBy": document.updated_by.display_name if document.updated_by_id else "",
    }


class StateApi(ApiView):
    http_method_names = ["get"]

    def get(self, request, doc_id):
        document = self.get_document(doc_id)
        other = services.touch_editing(document, request.user)
        return JsonResponse(_state_json(document, request.user, other=other))


class PingApi(ApiView):
    """Heartbeat while the editor is open: tells who else is editing and whether the draft moved on without us."""

    http_method_names = ["post"]

    def post(self, request, doc_id):
        document = self.get_document(doc_id)
        other = services.touch_editing(document, request.user)
        document.refresh_from_db()
        return JsonResponse({"ok": True, "revision": document.draft_rev, "otherEditor": other,
                             "updatedBy": document.updated_by.display_name if document.updated_by_id else ""})


class DraftApi(ApiView):
    http_method_names = ["post"]

    def post(self, request, doc_id):
        document = self.get_document(doc_id)
        try:
            data = self.json_body()
            revision = data.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError(gettext("The revision number is invalid."))
            document = services.save_draft(document.pk, data.get("document"), revision, request.user, overwrite=data.get("overwrite") is True)
        except ValueError as exc:
            return _err(str(exc))
        except DocumentInvalid as exc:
            return _err(gettext("The page structure is not valid."), 422, errors=exc.as_json())
        except services.ConflictError as exc:
            return _err(gettext("This page was edited and saved elsewhere. Reload the page."), 409, revision=exc.current_revision, by=exc.by)
        return JsonResponse({"ok": True, "revision": document.draft_rev})


class RenderApi(ApiView):
    """The live preview: the draft in the editor (not saved) drawn as an HTML fragment plus its CSS."""

    http_method_names = ["post"]

    def post(self, request, doc_id):
        document = self.get_document(doc_id)
        try:
            data = self.json_body()
            doc = services.validate_for(document, data.get("document"), request.user)
        except ValueError as exc:
            return _err(str(exc))
        except DocumentInvalid as exc:
            return _err(gettext("The page structure is not valid."), 422, errors=exc.as_json())
        result = render_document(doc, preview=True, make_files=False, validate=False)
        return JsonResponse({"ok": True, "html": result.html, "css": result.css, "frames": result.frame_hosts})


def _note(data):
    note = data.get("note", "")
    if not isinstance(note, str):
        raise ValueError(gettext("The note must be text."))
    return note.strip()[:200]


class VersionsApi(ApiView):
    """Save a version, submit for review, publish directly or unpublish."""

    http_method_names = ["post"]

    def post(self, request, doc_id):
        document = self.get_document(doc_id)
        try:
            data = self.json_body()
            action, note = data.get("action"), _note(data)
            if action == "save":
                services.save_version(document.pk, request.user, note)
            elif action == "submit":
                services.submit_for_review(document.pk, request.user, note)
            elif action == "publish":
                services.publish_draft(document.pk, request.user, note)
            elif action == "unpublish":
                services.unpublish(document.pk, request.user)
            else:
                raise ValueError(gettext("Unknown action."))
        except ValueError as exc:
            return _err(str(exc))
        except PermissionDenied:
            return _err(gettext("You do not have permission to approve and publish."), 403)
        except DocumentInvalid as exc:
            return _err(gettext("The layout is not complete yet; fix the items below."), 422, errors=exc.as_json())
        except services.WorkflowError as exc:
            return _err(str(exc), 409)
        document.refresh_from_db()
        return JsonResponse(_state_json(document, request.user))


class VersionActionApi(ApiView):
    """Restore (everyone who can edit), approve / reject / roll back (``studio.publish``)."""

    http_method_names = ["post"]

    def post(self, request, doc_id, number):
        document = self.get_document(doc_id)
        if not document.versions.filter(number=number).exists():
            raise Http404
        try:
            data = self.json_body()
            action, note = data.get("action"), _note(data)
            if action == "restore":
                services.restore(document.pk, number, request.user)
            elif action == "approve":
                services.approve(document.pk, number, request.user, note)
            elif action == "reject":
                services.reject(document.pk, number, request.user, note)
            elif action == "rollback":
                services.rollback(document.pk, number, request.user)
            else:
                raise ValueError(gettext("Unknown action."))
        except ValueError as exc:
            return _err(str(exc))
        except PermissionDenied:
            return _err(gettext("You do not have permission to approve and publish."), 403)
        except DocumentInvalid as exc:
            return _err(gettext("This version is not valid."), 422, errors=exc.as_json())
        except services.WorkflowError as exc:
            return _err(str(exc), 409)
        document.refresh_from_db()
        return JsonResponse(_state_json(document, request.user))


class ExportApi(ApiView):
    http_method_names = ["get"]

    def get(self, request, doc_id):
        document = self.get_document(doc_id)
        section = request.GET.get("section", "")
        source = "live" if request.GET.get("source") == "live" else "draft"
        try:
            payload = services.export_document(document, source=source, section_id=section if section else "")
        except DocumentInvalid as exc:
            return _err(gettext("This version is not valid."), 422, errors=exc.as_json())
        except services.WorkflowError as exc:
            return _err(str(exc), 404)
        name = f"{document.target.slug}{'-section' if section else ''}.studio.json"
        response = HttpResponse(json.dumps(payload, ensure_ascii=False, indent=1), content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ImportApi(ApiView):
    http_method_names = ["post"]

    def post(self, request, doc_id):
        document = self.get_document(doc_id)
        try:
            data = self.json_body(limit=LIMITS["max_json_bytes"] + 16384)
            mode = "append" if data.get("mode") == "append" else "replace"
            document, cleared = services.import_into_draft(document.pk, data.get("payload"), request.user, mode=mode)
        except ValueError as exc:
            return _err(str(exc))
        except DocumentInvalid as exc:
            return _err(gettext("The file is not a valid Studio layout."), 422, errors=exc.as_json())
        except services.WorkflowError as exc:
            return _err(str(exc), 422)
        state = _state_json(document, request.user)
        state["clearedMedia"] = cleared
        return JsonResponse(state)


class MediaListApi(ApiView):
    """Images (or videos) of the media library for the editor's picker; search and simple paging."""

    http_method_names = ["get"]
    PAGE = 36

    def get(self, request):
        kind = Media.Kind.VIDEO if request.GET.get("kind") == "video" else Media.Kind.IMAGE
        qs = Media.objects.filter(kind=kind).exclude(file="")
        ids = request.GET.get("ids", "")

        def item(m):
            return {"id": m.pk, "url": m.file.url, "title": m.display_title, "alt": m.alt_text, "kind": m.kind}

        if ids:
            wanted = safe_ids(ids.split(",")[:100], 100)
            return JsonResponse({"ok": True, "next": None, "items": [item(m) for m in Media.objects.filter(pk__in=wanted).exclude(file="")]})
        q = request.GET.get("q", "").strip()[:100]
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(original_name__icontains=q) | Q(alt_text__icontains=q))
        offset = safe_int(request.GET.get("offset"), 0, hi=10**7)
        rows = list(qs[offset: offset + self.PAGE + 1])
        return JsonResponse({"ok": True, "items": [item(m) for m in rows[: self.PAGE]], "next": offset + self.PAGE if len(rows) > self.PAGE else None})


def editor_messages() -> dict:
    from .editor_messages import messages

    return messages()
