import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin

from . import events, installer, registry, services, signing, sources, state
from .forms import build_settings_form, validate_form
from .integrity import IntegrityError, fingerprint, iter_files
from .models import PluginBackup, PluginEvent, PluginPackage, PluginRecord, TrustedKey
from .services import PluginError, SettingsInvalid

logger = logging.getLogger("ravin.plugins")


class PluginsAdminMixin(CapabilityRequiredMixin):
    required_capability = "plugins.manage"


# ------------------------------------------------------------------ مدیریت افزونه‌ها (فقط مدیر)
class PluginListView(PluginsAdminMixin, TemplateView):
    template_name = "plugins/list.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        ctx = super().get_context_data(**kwargs)
        rows = services.overview()
        for r in rows:
            r.new_version = sources.update_available(r.slug, r.record.version) if r.record else None
        ctx.update(rows=rows, web_install=settings.RAVIN_PLUGIN_WEB_INSTALL, safe_mode=settings.RAVIN_PLUGINS_SAFE_MODE, plugins_enabled=settings.RAVIN_PLUGINS_ENABLED,
                   recent_errors=PluginEvent.objects.filter(level="error").order_by("-created_at")[:5])
        return ctx


def _is_managed(path) -> bool:
    try:
        return path is not None and path.parent.resolve() == installer.install_root().resolve()
    except OSError:
        return False


class PluginDetailView(PluginsAdminMixin, TemplateView):
    template_name = "plugins/detail.html"

    def get_context_data(self, slug, **kwargs):
        ctx = super().get_context_data(**kwargs)
        row = services.get_row(slug)
        if row is None:
            raise Http404
        ctx.update(new_version=sources.update_available(slug, row.record.version) if row.record else None,
                   packages=PluginPackage.objects.filter(slug=slug)[:10], backups=PluginBackup.objects.filter(slug=slug)[:10],
                   web_install=settings.RAVIN_PLUGIN_WEB_INSTALL, managed=_is_managed(row.path))
        ctx.update(row=row, events=PluginEvent.objects.filter(slug=slug)[:15], has_settings=bool(row.record and row.record.manifest.get("settings")))
        if row.path is not None:
            try:
                ctx["fingerprint"] = fingerprint(row.path)
                ctx["files"] = [(str(p.relative_to(row.path).as_posix()), p.stat().st_size) for p in iter_files(row.path)]
            except IntegrityError as exc:
                ctx["integrity_error"] = str(exc)
        ctx["manifest_json"] = row.manifest.to_json() if row.manifest else None
        ctx["changed"] = bool(row.record and ctx.get("fingerprint") and ctx["fingerprint"] != row.record.fingerprint)
        return ctx


class PluginAction(PluginsAdminMixin, View):
    """پایهٔ عملیات تغییردهنده: فقط POST (با CSRF)."""

    http_method_names = ["post"]
    success = ""

    def perform(self, request, slug):
        raise NotImplementedError

    def post(self, request, slug):
        try:
            self.perform(request, slug)
        except PluginError as exc:
            messages.error(request, str(exc))
        else:
            if self.success:
                messages.success(request, self.success)
        return redirect("plugins:detail", slug=slug)


class InstallView(PluginsAdminMixin, TemplateView):
    """صفحهٔ تأیید نصب: مدیر آنچه افزونه اعلام می‌کند و اثرانگشت کد را می‌بیند؛ نصب فقط با تأیید صریح همان اثرانگشت انجام می‌شود."""

    template_name = "plugins/install.html"
    mode = "install"

    def _context(self, slug):
        row = services.get_row(slug)
        if row is None or row.manifest is None:
            raise Http404
        ctx = {"row": row, "mode": self.mode, "problems": list(row.problems)}
        try:
            ctx["fingerprint"] = fingerprint(row.path)
            ctx["files"] = [(str(p.relative_to(row.path).as_posix()), p.stat().st_size) for p in iter_files(row.path)]
        except IntegrityError as exc:
            ctx["problems"].append(str(exc))
        if self.mode == "update":
            if row.record is None:
                raise Http404
            ctx["problems"] = list(row.problems) + services.check_compat(row.manifest)
        return ctx

    def get_context_data(self, slug, **kwargs):
        return {**super().get_context_data(**kwargs), **self._context(slug)}

    def post(self, request, slug):
        if request.POST.get("trust") != "on":
            messages.error(request, "برای ادامه باید تأیید کنید که کد افزونه را بررسی کرده و به آن اعتماد دارید.")
            return redirect(request.path)
        fp = request.POST.get("fingerprint", "")
        try:
            if self.mode == "install":
                services.install(slug, request.user, fp, request)
                messages.success(request, "افزونه نصب شد. برای استفاده، آن را فعال کنید.")
            else:
                services.approve_update(slug, request.user, fp, request)
                messages.success(request, "تغییرات تأیید شد. برای استفاده، افزونه را دوباره فعال کنید.")
        except PluginError as exc:
            messages.error(request, str(exc))
            return redirect(request.path)
        return redirect("plugins:detail", slug=slug)


class UpdateView(InstallView):
    mode = "update"


class ActivateView(PluginAction):
    success = "افزونه فعال شد."

    def perform(self, request, slug):
        services.activate(slug, request.user, request)


class DeactivateView(PluginAction):
    success = "افزونه غیرفعال شد."

    def perform(self, request, slug):
        services.deactivate(slug, request.user, request)


class UninstallView(PluginAction):
    success = "افزونه حذف شد."

    def perform(self, request, slug):
        services.uninstall(slug, request.user, purge_data=request.POST.get("purge") == "on", remove_files=request.POST.get("remove_files") == "on",
                           request=request)

    def post(self, request, slug):
        try:
            self.perform(request, slug)
        except PluginError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:detail", slug=slug)
        messages.success(request, self.success)
        return redirect("plugins:list")


class SettingsView(PluginsAdminMixin, View):
    def _setup(self, slug):
        rec = PluginRecord.objects.filter(slug=slug).first()
        if rec is None:
            raise Http404
        manifest, current = services.current_settings(rec)
        if not manifest.settings:
            raise Http404
        return rec, manifest, current

    def get(self, request, slug):
        rec, manifest, current = self._setup(slug)
        return render(request, "plugins/settings.html", {"rec": rec, "manifest": manifest, "form": build_settings_form(manifest.settings, initial=current)})

    def post(self, request, slug):
        rec, manifest, _current = self._setup(slug)
        form = build_settings_form(manifest.settings, data=request.POST)
        clean, errors = validate_form(form)
        if not errors:
            try:
                services.save_settings(slug, request.user, clean, request)
            except (PluginError, SettingsInvalid) as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "تنظیمات ذخیره شد.")
                return redirect("plugins:settings", slug=slug)
        return render(request, "plugins/settings.html", {"rec": rec, "manifest": manifest, "form": form}, status=400 if errors else 200)


class EventsView(PluginsAdminMixin, TemplateView):
    template_name = "plugins/events.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = PluginEvent.objects.all()
        slug, level = self.request.GET.get("plugin", ""), self.request.GET.get("level", "")
        if slug:
            qs = qs.filter(slug=slug)
        if level in ("info", "warning", "error"):
            qs = qs.filter(level=level)
        page = Paginator(qs, 40).get_page(self.request.GET.get("page"))
        query = "&".join(f"{k}={v}" for k, v in (("plugin", slug), ("level", level)) if v)
        ctx.update(events=page, page_obj=page, paginator=page.paginator, is_paginated=page.has_other_pages(), query_string=query,
                   slugs=PluginEvent.objects.order_by().values_list("slug", flat=True).distinct(), selected_slug=slug, selected_level=level)
        return ctx


# ------------------------------------------------------------------ توزیع‌کنندهٔ صفحات مدیریتی افزونه‌ها
def _unavailable(request, slug, *, status=503, reason="افزونه در دسترس نیست."):
    return render(request, "plugins/error.html", {"plugin_slug": slug, "reason": reason}, status=status)


def admin_page(request, slug, key):
    """هسته همهٔ بررسی‌ها را انجام می‌دهد؛ افزونه فقط بعد از عبور از مجوز فراخوانی می‌شود و خطایش مهار می‌شود."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    info = state.get_active(slug)
    page = info.manifest.page(key) if info else None
    if page is None:
        raise Http404
    if not request.user.has_cap(page["capability"]):  # دور زدن ممکن نیست: مجوز اعلام‌شده در manifest را هسته اعمال می‌کند
        raise PermissionDenied
    loaded = registry.get(slug)
    if loaded is None:
        return _unavailable(request, slug)
    try:
        response = loaded.pages[key](request, loaded.api)
        if not isinstance(response, HttpResponseBase):
            raise TypeError("ویو باید HttpResponse برگرداند.")
    except (Http404, PermissionDenied):
        raise
    except Exception as exc:  # noqa: BLE001
        disabled = events.record_error(slug, f"admin:{key}", exc)
        return _unavailable(request, slug, status=500, reason=("افزونه به‌دلیل خطاهای مکرر غیرفعال شد." if disabled else "در اجرای این صفحه خطایی رخ داد؛ سایر بخش‌های پنل سالم است."))
    response["Cache-Control"] = "private, no-store"
    return response


def public_route(request, slug, path=""):
    path = path.strip("/")
    info = state.get_active(slug)
    route = None
    if info:
        route = next((r for r in info.manifest.public_routes if r["path"].strip("/") == path), None)
    if route is None:
        raise Http404
    if request.method not in route["methods"]:
        return HttpResponseNotAllowed(route["methods"])
    loaded = registry.get(slug)
    if loaded is None:
        raise Http404
    try:
        response = loaded.routes[route["path"]](request, loaded.api)
        if not isinstance(response, HttpResponseBase):
            raise TypeError("ویو باید HttpResponse برگرداند.")
    except (Http404, PermissionDenied):
        raise
    except Exception as exc:  # noqa: BLE001
        events.record_error(slug, f"public:{path or '/'}", exc)
        return HttpResponse("<!doctype html><html lang=\"fa\" dir=\"rtl\"><meta charset=\"utf-8\"><title>خطا</title><p>این بخش موقتاً در دسترس نیست.</p></html>",
                            status=500, content_type="text/html; charset=utf-8")
    if not response.has_header("Cache-Control"):
        response["Cache-Control"] = "private, no-store"
    return response


# ------------------------------------------------------------------ نصب از مخزن / فایل، ارتقا، بازگشت، کلیدها
def _flash_result(request, kind, slug):
    if kind == "installed":
        messages.success(request, "بسته دریافت، اعتبارسنجی و جایگذاری شد. اکنون مشخصات و فایل‌ها را بازبینی و نصب را تأیید کنید.")
        return redirect("plugins:install", slug=slug)
    messages.success(request, "نسخهٔ جدید جایگزین شد و نسخهٔ قبلی پشتیبان‌گیری شد. افزونه تا تأیید تغییرات غیرفعال است.")
    return redirect("plugins:update", slug=slug)


class InstallPackageView(PluginsAdminMixin, View):
    def _ctx(self, request, **extra):
        from django.conf import settings

        return {"web_install": settings.RAVIN_PLUGIN_WEB_INSTALL, "has_registry": sources.has_source(), "trusted_count": len(signing.trusted_keys()),
                "allow_unsigned": settings.RAVIN_PLUGINS_ALLOW_UNSIGNED, "prefill_slug": request.GET.get("slug", "")[:40],
                "prefill_version": request.GET.get("version", "")[:20], **extra}

    def get(self, request):
        return render(request, "plugins/install_package.html", self._ctx(request))

    def post(self, request):
        from django.conf import settings

        op = request.POST.get("op")
        try:
            if op == "registry":
                slug = request.POST.get("slug", "").strip()[:40]
                if not slug:
                    raise PluginError("شناسهٔ افزونه را وارد کنید.")
                kind, slug = installer.install_from_registry(slug, request.POST.get("version", "").strip()[:20] or None, request.user)
            elif op == "local":
                f = request.FILES.get("package")
                if f is None:
                    raise PluginError("فایل zip را انتخاب کنید.")
                if f.size > settings.RAVIN_REGISTRY_MAX_PACKAGE_BYTES:
                    raise PluginError("حجم فایل از سقف مجاز بیشتر است.")
                kind, slug = installer.install_from_file(f.read(), request.user, signature=request.POST.get("signature", "")[:200],
                                                         key_id=request.POST.get("key_id", "")[:16], upgrade_existing=request.POST.get("upgrade") == "on")
            else:
                raise PermissionDenied
        except PluginError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:install_package")
        services._audit(request, f"بسته افزونهٔ «{slug}» ({'نصب' if kind == 'installed' else 'ارتقا'}) از طریق نصب‌کننده جایگذاری شد", slug)
        return _flash_result(request, kind, slug)


class UpgradeView(PluginsAdminMixin, View):
    http_method_names = ["post"]

    def post(self, request, slug):
        try:
            kind, _ = installer.install_from_registry(slug, request.POST.get("version", "").strip()[:20] or None, request.user, upgrade_existing=True)
        except PluginError as exc:
            messages.error(request, str(exc))
            return redirect("plugins:detail", slug=slug)
        services._audit(request, f"ارتقای افزونهٔ «{slug}» از مخزن", slug)
        return _flash_result(request, kind, slug)


class RollbackView(PluginsAdminMixin, View):
    http_method_names = ["post"]

    def post(self, request, slug, pk):
        if not PluginBackup.objects.filter(pk=pk, slug=slug).exists():
            raise Http404
        try:
            installer.rollback(pk, request.user, confirm_data_loss=request.POST.get("confirm_data_loss") == "on")
        except PluginError as exc:
            messages.error(request, str(exc))
        else:
            services._audit(request, f"بازگشت افزونهٔ «{slug}» از پشتیبان #{pk}", slug)
            messages.success(request, "بازگشت انجام شد. افزونه غیرفعال است؛ پس از بررسی دوباره فعال کنید.")
        return redirect("plugins:detail", slug=slug)


class TrustedKeysView(CapabilityRequiredMixin, View):
    """کلیدهای عمومی مورد اعتماد (ریشهٔ اعتماد). فقط مدیرکل؛ کلید خصوصی هرگز اینجا وارد نمی‌شود."""

    required_capability = "plugins.trust"

    def _render(self, request, status=200):
        return render(request, "plugins/trusted_keys.html", {"env_keys": [k for k in signing.trusted_keys().values() if k.source == "env"],
                                                            "db_keys": TrustedKey.objects.all()}, status=status)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        op = request.POST.get("op")
        if op == "add":
            label = request.POST.get("label", "").strip()[:80]
            try:
                pub = signing.normalize_public_key(request.POST.get("public_key", ""))
            except signing.SigningError as exc:
                messages.error(request, str(exc))
                return self._render(request, 400)
            if not label:
                messages.error(request, "برچسب الزامی است.")
                return self._render(request, 400)
            kid = signing.key_id(pub)
            _, created = TrustedKey.objects.get_or_create(key_id=kid, defaults={"public_key": pub, "label": label, "added_by": request.user})
            events.log_event("_keys", "key_added" if created else "key_exists", f"کلید {kid} ({label}) {'افزوده شد' if created else 'از قبل بود'}.", actor=request.user)
            messages.success(request, f"کلید {kid} ثبت شد." if created else "این کلید از قبل ثبت بوده است.")
        elif op in ("toggle", "delete"):
            k = TrustedKey.objects.filter(pk=request.POST.get("pk")).first()
            if k is None:
                raise Http404
            if op == "delete":
                k.delete()
            else:
                k.is_active = not k.is_active
                k.save(update_fields=["is_active"])
            events.log_event("_keys", f"key_{op}", f"کلید {k.key_id} ({k.label}) {op}.", actor=request.user)
            messages.success(request, "انجام شد.")
        return redirect("plugins:trusted_keys")
