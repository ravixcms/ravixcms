"""پنل مخزن: Plugin Directory (برای مدیر سایت)، انتشار (برای ناشر مجاز)، مدیریت مخزن (مدیرکل)."""
from django.utils.translation import gettext
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.plugins.models import PluginRecord

from . import services
from .forms import CategoryForm, PluginMetaForm, PublisherForm, TokenForm, UploadForm
from .models import Category, HubPlugin, HubVersion, Publisher, PublisherToken


def _audit(request, obj, message):
    log_event(request, AuditLog.Action.UPDATE, obj, message)


# ------------------------------------------------------------------ Directory
class DirectoryMixin(CapabilityRequiredMixin):
    required_capability = "plugins.manage"


class DirectoryView(DirectoryMixin, View):
    def get(self, request):
        q = request.GET.get("q", "").strip()[:100]
        cat = request.GET.get("category", "")[:30]
        compatible = request.GET.get("compatible") == "1"
        items = services.search(q, cat, compatible)
        installed = {r.slug: r for r in PluginRecord.objects.all()}
        rows = [{"plugin": p, "latest": l, "compat_latest": c, "installed": installed.get(p.slug)} for p, l, c in items]
        page = Paginator(rows, 12).get_page(request.GET.get("page"))
        qs = "&".join(f"{k}={v}" for k, v in (("q", q), ("category", cat), ("compatible", "1" if compatible else "")) if v)
        return render(request, "pluginhub/directory.html", {"page_obj": page, "paginator": page.paginator, "is_paginated": page.has_other_pages(), "q": q,
                                                          "category": cat, "compatible": compatible, "categories": Category.objects.all(), "query_string": qs,
                                                          "total": len(rows)})


class DirectoryDetailView(DirectoryMixin, View):
    def get(self, request, slug):
        p = get_object_or_404(HubPlugin.objects.select_related("category", "publisher"), slug=slug, is_listed=True, publisher__is_active=True)
        vs = sorted([v for v in p.versions.all() if v.status != HubVersion.Status.DRAFT], key=lambda v: v.parsed, reverse=True)
        if not any(v.status == HubVersion.Status.PUBLISHED for v in vs):
            raise PermissionDenied
        rows = [{"v": v, "compat": services.version_compat(v)} for v in vs]
        base = request.build_absolute_uri("/").rstrip("/")
        return render(request, "pluginhub/directory_detail.html", {"p": p, "rows": rows, "installed": PluginRecord.objects.filter(slug=slug).first(),
                                                                  "latest": services.latest_published(p, compatible_only=True), "registry_url": base})


# ------------------------------------------------------------------ ناشر
class PublisherMixin:
    """کاربر واردشده + رکورد فعال Publisher. نقش «مدیر» به‌تنهایی کافی نیست."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        self.publisher = services.get_publisher(request.user)
        if self.publisher is None:
            return render(request, "pluginhub/not_publisher.html", status=403)
        return super().dispatch(request, *args, **kwargs)

    def own_plugin(self, slug):
        return get_object_or_404(HubPlugin, slug=slug, publisher=self.publisher)  # افزونهٔ ناشر دیگر ⇒ ۴۰۴


class MyPluginsView(PublisherMixin, View):
    def get(self, request):
        return render(request, "pluginhub/my_plugins.html", {"plugins": self.publisher.plugins.prefetch_related("versions"), "publisher": self.publisher})


class UploadView(PublisherMixin, View):
    def get(self, request):
        return render(request, "pluginhub/upload.html", {"form": UploadForm()})

    def post(self, request):
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                v = services.upload_version(self.publisher, request.user, form.cleaned_data["package"].read(), changelog=form.cleaned_data["changelog"],
                                            publish=form.cleaned_data["publish"])
            except services.HubError as exc:
                form.add_error(None, str(exc))
            else:
                _audit(request, v.plugin, (gettext("Version %(version)s of plugin “%(slug)s” was uploaded to the registry (%(status)s)") % {"version": v.version, "slug": v.plugin.slug, "status": v.get_status_display()}))
                messages.success(request, (gettext("Version %(version)s was registered (%(status)s).") % {"version": v.version, "status": v.get_status_display()}))
                return redirect("pluginhub:my_plugin", slug=v.plugin.slug)
        return render(request, "pluginhub/upload.html", {"form": form}, status=400)


class MyPluginView(PublisherMixin, View):
    def _ctx(self, p, form=None):
        return {"p": p, "form": form or PluginMetaForm(instance=p), "versions": sorted(p.versions.all(), key=lambda v: v.parsed, reverse=True)}

    def get(self, request, slug):
        return render(request, "pluginhub/my_plugin.html", self._ctx(self.own_plugin(slug)))

    def post(self, request, slug):
        p = self.own_plugin(slug)
        form = PluginMetaForm(request.POST, instance=p)
        if form.is_valid():
            form.save()
            _audit(request, p, (gettext("The details of plugin “%(slug)s” in the registry were edited") % {"slug": p.slug}))
            messages.success(request, gettext("Saved."))
            return redirect("pluginhub:my_plugin", slug=slug)
        return render(request, "pluginhub/my_plugin.html", self._ctx(p, form), status=400)


class VersionActionView(PublisherMixin, View):
    def post(self, request, slug, version, action):
        p = self.own_plugin(slug)
        v = get_object_or_404(HubVersion, plugin=p, version=version)
        try:
            if action == "publish":
                services.publish_version(v, self.publisher)
                msg = gettext("Published.")
            elif action == "yank":
                services.yank_version(v, self.publisher, request.POST.get("reason", ""))
                msg = gettext("Withdrawn.")
            elif action == "delete":
                services.delete_draft(v, self.publisher)
                msg = gettext("Draft deleted.")
            else:
                raise PermissionDenied
        except services.HubError as exc:
            messages.error(request, str(exc))
        else:
            _audit(request, p, (gettext("Version %(version)s of plugin “%(slug)s”: %(msg)s") % {"version": version, "slug": slug, "msg": msg}))
            messages.success(request, (gettext("Version %(version)s %(msg)s") % {"version": version, "msg": msg}))
        return redirect("pluginhub:my_plugin", slug=slug)


class TokensView(PublisherMixin, View):
    def _render(self, request, form=None, raw=None, status=200):
        return render(request, "pluginhub/tokens.html", {"tokens": self.publisher.tokens.all(), "form": form or TokenForm(), "raw": raw}, status=status)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        form = TokenForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        tok, raw = PublisherToken.issue(self.publisher, form.cleaned_data["name"])
        _audit(request, self.publisher, (gettext("Publishing token “%(name)s” was created") % {"name": tok.name}))
        resp = self._render(request, raw=raw)  # مقدار اصلی فقط در همین پاسخ؛ هرگز ذخیره/لاگ نمی‌شود
        resp["Cache-Control"] = "no-store"
        return resp


class TokenRevokeView(PublisherMixin, View):
    def post(self, request, pk):
        from django.utils import timezone
        tok = get_object_or_404(PublisherToken, pk=pk, publisher=self.publisher)
        tok.revoked_at = tok.revoked_at or timezone.now()
        tok.save(update_fields=["revoked_at"])
        _audit(request, self.publisher, (gettext("Token “%(name)s” was revoked") % {"name": tok.name}))
        messages.success(request, gettext("Token revoked."))
        return redirect("pluginhub:tokens")


# ------------------------------------------------------------------ مدیریت مخزن (مدیرکل)
class RegistryAdminMixin(CapabilityRequiredMixin):
    required_capability = "registry.manage"


class AdminPublishersView(RegistryAdminMixin, View):
    def _render(self, request, form=None, status=200):
        return render(request, "pluginhub/admin_publishers.html", {"publishers": Publisher.objects.select_related("user"), "form": form or PublisherForm()}, status=status)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        op = request.POST.get("op")
        if op == "add":
            form = PublisherForm(request.POST)
            if not form.is_valid():
                return self._render(request, form, 400)
            pub, created = Publisher.objects.get_or_create(user=form.user, defaults={"display_name": form.cleaned_data["display_name"], "website": form.cleaned_data["website"]})
            if not created:
                pub.is_active = True
                pub.save(update_fields=["is_active"])
            _audit(request, pub, (gettext("Publisher “%(display_name)s” was authorized") % {"display_name": pub.display_name}))
            messages.success(request, gettext("Publisher authorized."))
        elif op in ("toggle_active", "toggle_verified"):
            pub = get_object_or_404(Publisher, pk=request.POST.get("pk"))
            field = "is_active" if op == "toggle_active" else "is_verified"
            setattr(pub, field, not getattr(pub, field))
            pub.save(update_fields=[field])
            _audit(request, pub, (gettext("Publisher “%(display_name)s”: %(field)s=%(pub)s") % {"display_name": pub.display_name, "field": field, "pub": getattr(pub, field)}))
            messages.success(request, gettext("Saved."))
        return redirect("pluginhub:admin_publishers")


class AdminCategoriesView(RegistryAdminMixin, View):
    def _render(self, request, form=None, status=200):
        return render(request, "pluginhub/admin_categories.html", {"categories": Category.objects.all(), "form": form or CategoryForm()}, status=status)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        if request.POST.get("op") == "delete":
            Category.objects.filter(pk=request.POST.get("pk")).delete()
            messages.success(request, gettext("Deleted."))
            return redirect("pluginhub:admin_categories")
        form = CategoryForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, 400)
        form.save()
        messages.success(request, gettext("Category added."))
        return redirect("pluginhub:admin_categories")


class AdminPluginsView(RegistryAdminMixin, View):
    def get(self, request):
        return render(request, "pluginhub/admin_plugins.html", {"plugins": HubPlugin.objects.select_related("publisher", "category").prefetch_related("versions")})

    def post(self, request):
        op = request.POST.get("op")
        if op == "toggle_listed":
            p = get_object_or_404(HubPlugin, pk=request.POST.get("pk"))
            p.is_listed = not p.is_listed
            p.save(update_fields=["is_listed", "updated_at"])
            _audit(request, p, (gettext("Plugin “%(slug)s” was %(value)s") % {"slug": p.slug, "value": (gettext("listed") if p.is_listed else gettext("removed from the listing"))}))
        elif op == "yank":
            v = get_object_or_404(HubVersion, pk=request.POST.get("pk"))
            services.yank_version(v, None, request.POST.get("reason", gettext("by the registry administrator")), is_admin=True)
            _audit(request, v.plugin, (gettext("Version %(version)s of plugin “%(slug)s” was withdrawn by the registry administrator") % {"version": v.version, "slug": v.plugin.slug}))
        messages.success(request, gettext("Done."))
        return redirect("pluginhub:admin_plugins")
