import json

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.content.models import Page
from apps.core.templatetags.ravix_ui import jalali_datetime
from apps.media_library.models import Media
from apps.public import builders as pub
from apps.public.rendering import render_page
from apps.public.views import _crumbs

from . import services
from .models import DesignVersion, PageDesign
from .render import BASE_CSS, render_document
from .schema import LIMITS, DocumentInvalid, client_specs, validate_document


# ============================================================ صفحات پنل
class PageListView(CapabilityRequiredMixin, ListView):
    required_capability = "builder.edit"
    template_name = "builder/list.html"
    context_object_name = "pages"
    paginate_by = 20

    def get_queryset(self):
        qs = Page.objects.select_related("design", "design__published_version").annotate(
            pending=Count("design__versions", filter=Q(design__versions__state=DesignVersion.State.PENDING)),
        )
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q))
        if self.request.GET.get("pending") == "1":
            qs = qs.filter(pending__gt=0)
        return qs.order_by("-pending", "title")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "").strip()
        ctx["only_pending"] = self.request.GET.get("pending") == "1"
        ctx["can_publish"] = self.request.user.has_cap("builder.publish")
        return ctx


class EditorView(CapabilityRequiredMixin, TemplateView):
    required_capability = "builder.edit"
    template_name = "builder/editor.html"

    def get_context_data(self, **kwargs):
        page = get_object_or_404(Page, pk=kwargs["page_id"])
        design = services.get_design(page)
        user = self.request.user
        b = lambda name, *a: reverse(f"builder:{name}", args=[page.pk, *a])  # noqa: E731
        config = {
            "pageId": page.pk,
            "pageTitle": page.title,
            "csrf": get_token(self.request),
            "canPublish": user.has_cap("builder.publish"),
            "specs": client_specs(),
            "urls": {
                "state": b("api_state"), "draft": b("api_draft"), "versions": b("api_versions"),
                "versionAction": b("api_version", 0).rsplit("0/", 1)[0],  # + «{number}/»
                "preview": b("preview"), "media": reverse("builder:api_media"), "list": reverse("builder:list"),
                "pageEdit": reverse("content:page_update", args=[page.pk]),
            },
            "pageLive": page.is_live,
            "pageUrl": pub.page_url(page),
        }
        return {"page": page, "design": design, "config": config, "base_css": mark_safe(BASE_CSS)}


class PreviewView(CapabilityRequiredMixin, View):
    """پیش‌نمایش واقعی صفحه با قالب فعال (برای iframe هم‌مبدأ در ویرایشگر). ``?version=N`` نسخه‌ی مشخصی را نشان می‌دهد."""

    required_capability = "builder.edit"
    http_method_names = ["get"]

    def get(self, request, page_id):
        page = get_object_or_404(Page, pk=page_id)
        design = get_object_or_404(PageDesign, page=page)
        number = request.GET.get("version", "")
        if number.isdigit():
            version = get_object_or_404(DesignVersion, design=design, number=int(number))
            doc = version.document
        else:
            doc = design.draft
        html = render_document(doc)
        try:
            show_title = bool(validate_document(doc)["meta"]["show_title"])
        except DocumentInvalid:
            show_title = True
        item = pub.page_item(page, body_html=html or "<p>طرح نامعتبر است.</p>", builder=True, show_title=show_title)

        def seo(site):
            return {"title": f"پیش‌نمایش: {page.title}", "robots": "noindex,nofollow", "description": "", "canonical": ""}

        ctx = {"page": item, "breadcrumbs": _crumbs(("خانه", "/"), (page.title, ""))}
        return render_page(request, "page_detail.html", ctx, embedded=True, seo_factory=seo)


# ============================================================ API (JSON)
def _err(message, status=400, **extra):
    return JsonResponse({"ok": False, "error": message, **extra}, status=status)


class ApiView(View):
    """پایه‌ی APIهای JSON: خطای JSON (نه ریدایرکت)، CSRF توسط middleware، سقف حجم بدنه و پارس امن JSON."""

    required_capability = "builder.edit"

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return _err("ابتدا وارد حساب کاربری شوید.", 401)
        if not user.has_cap(self.required_capability):
            return _err("اجازه‌ی این کار را ندارید.", 403)
        return super().dispatch(request, *args, **kwargs)

    def json_body(self):
        request = self.request
        if request.content_type != "application/json":
            raise ValueError("نوع محتوا باید application/json باشد.")
        if len(request.body) > LIMITS["max_json_bytes"] + 4096:
            raise ValueError("حجم داده بیش از حد مجاز است.")
        try:
            data = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("JSON نامعتبر است.")
        if not isinstance(data, dict):
            raise ValueError("بدنه‌ی درخواست باید یک شیء JSON باشد.")
        return data

    def get_design(self, page_id):
        page = get_object_or_404(Page, pk=page_id)
        return page, services.get_design(page)


def _version_json(v: DesignVersion, design: PageDesign) -> dict:
    return {
        "number": v.number, "state": v.state, "state_label": v.get_state_display(), "is_auto": v.is_auto, "note": v.note,
        "created_by": v.created_by.display_name if v.created_by else "—", "created_at": jalali_datetime(v.created_at),
        "reviewed_by": v.reviewed_by.display_name if v.reviewed_by else "", "review_note": v.review_note,
        "is_live": design.published_version_id == v.pk,
    }


def _state_json(page: Page, design: PageDesign, user) -> dict:
    versions = list(design.versions.select_related("created_by", "reviewed_by")[:60])
    live = next((v.number for v in versions if v.pk == design.published_version_id), None)
    if live is None and design.published_version_id:
        live = design.published_version.number
    return {
        "ok": True, "document": design.draft, "revision": design.revision, "live": live,
        "versions": [_version_json(v, design) for v in versions], "canPublish": user.has_cap("builder.publish"),
        "pageLive": page.is_live,
    }


class StateApi(ApiView):
    http_method_names = ["get"]

    def get(self, request, page_id):
        page, design = self.get_design(page_id)
        return JsonResponse(_state_json(page, design, request.user))


class DraftApi(ApiView):
    http_method_names = ["post"]

    def post(self, request, page_id):
        page, design = self.get_design(page_id)
        try:
            data = self.json_body()
            revision = data.get("revision")
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise ValueError("شماره‌ی ویرایش نامعتبر است.")
            design = services.save_draft(design.pk, data.get("document"), revision, request.user)
        except ValueError as exc:
            return _err(str(exc))
        except DocumentInvalid as exc:
            return _err("ساختار صفحه معتبر نیست.", 422, errors=exc.as_json())
        except services.ConflictError as exc:
            return _err("این صفحه در جای دیگری ویرایش و ذخیره شده است. صفحه را دوباره بارگذاری کنید.", 409, revision=exc.current_revision)
        return JsonResponse({"ok": True, "revision": design.revision})


class VersionsApi(ApiView):
    """ذخیره‌ی نسخه، ارسال برای تأیید، انتشار مستقیم یا لغو انتشار."""

    http_method_names = ["post"]

    def post(self, request, page_id):
        page, design = self.get_design(page_id)
        try:
            data = self.json_body()
            action = data.get("action")
            note = data.get("note", "")
            if not isinstance(note, str):
                raise ValueError("یادداشت باید متن باشد.")
            note = note.strip()[:200]
            if action == "save":
                services.save_version(design.pk, request.user, note)
            elif action == "submit":
                services.submit_for_review(design.pk, request.user, note)
            elif action == "publish":
                services.publish_draft(design.pk, request.user, note)
            elif action == "unpublish":
                services.unpublish(design.pk, request.user)
            else:
                raise ValueError("عملیات ناشناخته است.")
        except ValueError as exc:
            return _err(str(exc))
        except PermissionDenied:
            return _err("اجازه‌ی تأیید و انتشار را ندارید.", 403)
        except DocumentInvalid as exc:
            return _err("طرح هنوز کامل نیست؛ موارد زیر را اصلاح کنید.", 422, errors=exc.as_json())
        except services.WorkflowError as exc:
            return _err(str(exc), 409)
        page.refresh_from_db()
        design.refresh_from_db()
        return JsonResponse(_state_json(page, design, request.user))


class VersionActionApi(ApiView):
    """بازیابی (همه‌ی ویرایشگران)، تأیید و رد (فقط ``builder.publish``)."""

    http_method_names = ["post"]

    def post(self, request, page_id, number):
        page, design = self.get_design(page_id)
        if not design.versions.filter(number=number).exists():
            raise Http404
        try:
            data = self.json_body()
            action = data.get("action")
            note = data.get("note", "")
            if not isinstance(note, str):
                raise ValueError("یادداشت باید متن باشد.")
            note = note.strip()[:200]
            if action == "restore":
                services.restore(design.pk, number, request.user)
            elif action == "approve":
                services.approve(design.pk, number, request.user, note)
            elif action == "reject":
                services.reject(design.pk, number, request.user, note)
            else:
                raise ValueError("عملیات ناشناخته است.")
        except ValueError as exc:
            return _err(str(exc))
        except PermissionDenied:
            return _err("اجازه‌ی تأیید و انتشار را ندارید.", 403)
        except DocumentInvalid as exc:
            return _err("این نسخه معتبر نیست.", 422, errors=exc.as_json())
        except services.WorkflowError as exc:
            return _err(str(exc), 409)
        page.refresh_from_db()
        design.refresh_from_db()
        return JsonResponse(_state_json(page, design, request.user))


class MediaListApi(ApiView):
    """فهرست تصاویر کتابخانه برای انتخاب در ویرایشگر (فقط تصویر؛ جستجو و صفحه‌بندی ساده)."""

    http_method_names = ["get"]
    PAGE = 36

    def get(self, request):
        qs = Media.objects.filter(kind=Media.Kind.IMAGE).exclude(file="")
        ids = request.GET.get("ids", "")
        if ids:  # دریافت نشانی چند تصویر مشخص (برای نمایش در بوم)؛ حداکثر ۱۰۰ شناسه‌ی عددی
            wanted = [int(x) for x in ids.split(",")[:100] if x.isdigit()]
            return JsonResponse({"ok": True, "next": None, "items": [
                {"id": m.pk, "url": m.file.url, "title": m.display_title, "alt": m.alt_text} for m in qs.filter(pk__in=wanted)]})
        q = request.GET.get("q", "").strip()[:100]
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(original_name__icontains=q) | Q(alt_text__icontains=q))
        offset = int(request.GET["offset"]) if request.GET.get("offset", "").isdigit() else 0
        items = list(qs[offset: offset + self.PAGE + 1])
        return JsonResponse({
            "ok": True,
            "items": [{"id": m.pk, "url": m.file.url, "title": m.display_title, "alt": m.alt_text} for m in items[: self.PAGE]],
            "next": offset + self.PAGE if len(items) > self.PAGE else None,
        })
