from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.core.models import SiteSettings

from . import installer, preview, registry, services
from .constants import (
    DEFAULT_THEME,
    FONT_SIZES,
    FONTS,
    HOME_SECTION_TYPES,
    MAX_EXTRACTED_BYTES,
    MAX_FILES,
    MAX_ZIP_BYTES,
    MENU_LOCATIONS,
)
from .forms import (
    AppearanceForm,
    LayoutForm,
    MenuFormSet,
    SectionFormSet,
    ThemeUploadForm,
    menu_from_formset,
    menu_initial,
    section_initial,
    sections_from_formset,
)
from .models import ThemeConfig
from .services import ThemeError


class ThemesAdminMixin(CapabilityRequiredMixin):
    required_capability = "themes.manage"


class ThemeActionView(ThemesAdminMixin, View):
    """پایه عملیات‌های تغییردهنده: فقط POST (با CSRF)."""

    http_method_names = ["post"]
    success_message = ""

    def perform(self, request, **kwargs):
        raise NotImplementedError

    def post(self, request, **kwargs):
        try:
            result = self.perform(request, **kwargs)
        except ThemeError as exc:
            messages.error(request, str(exc))
        else:
            if self.success_message:
                messages.success(request, self.success_message)
            if result is not None:
                return result
        return redirect(self.get_redirect_url(request, **kwargs))

    def get_redirect_url(self, request, **kwargs):
        return reverse("themes:list")


# ------------------------------------------------------------------ فهرست قالب‌ها
class ThemeListView(ThemesAdminMixin, TemplateView):
    template_name = "themes/theme_list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cfg = ThemeConfig.load()
        themes = registry.get_themes(refresh=True)  # هر بار پوشه‌ها دوباره بررسی می‌شوند
        active = themes.get(cfg.active_theme)
        installed = self.request.GET.get("installed", "")
        items = []
        for t in themes.values():
            has_static = (t.path / "static").is_dir()
            items.append(
                {
                    "info": t,
                    "is_active": t.slug == cfg.active_theme,
                    # فایل‌های استاتیک قالب فقط پس از collectstatic (در فرایند تازه) در STATIC_ROOT می‌آیند؛ نصب وب آن را اجرا نمی‌کند.
                    "static_pending": bool(
                        t.valid and has_static and not settings.DEBUG and not (Path(settings.STATIC_ROOT) / "themes" / t.slug).is_dir()
                    ),
                }
            )
        ctx.update(
            cfg=cfg,
            themes=items,
            active_missing=active is None or not active.valid,
            default_slug=DEFAULT_THEME,
            draft_theme=(cfg.draft or {}).get("theme") if cfg.draft else None,
            can_install=self.request.user.has_cap(installer.CAPABILITY),
            web_install=bool(settings.RAVIX_THEME_WEB_INSTALL),
            upload_form=ThemeUploadForm(),
            limits={"zip_bytes": MAX_ZIP_BYTES, "zip_mb": MAX_ZIP_BYTES // (1024 * 1024), "extracted_mb": MAX_EXTRACTED_BYTES // (1024 * 1024), "files": MAX_FILES},
            installed=installed if installed in themes else "",
            installed_static_pending=any(i["static_pending"] and i["info"].slug == installed for i in items),
        )
        return ctx


class ThemeUploadView(ThemeListView):
    """بارگذاری و نصب قالب از ZIP: فقط POST (با CSRF)، فقط قابلیت ``themes.install``؛ نتیجهٔ خطا در همین صفحه نمایش داده می‌شود."""

    required_capability = installer.CAPABILITY
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_cap(self.required_capability):
            installer._audit(request, "رد شد", "کاربر بدون مجوز «themes.install» تلاش کرد قالب نصب کند.")
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = ThemeUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._fail(form.errors.get("theme_zip", ["فایل نامعتبر است."])[0])
        try:
            result = installer.install_theme_zip(request, form.cleaned_data["theme_zip"])
        except installer.ThemeInstallError as exc:
            return self._fail(exc.message, exc.details)
        messages.success(request, f"قالب «{result.name}» نصب شد و در فهرست زیر قرار گرفت؛ فعال نشده است و هر زمان خواستید خودتان فعالش کنید.")
        return redirect(f"{reverse('themes:list')}?installed={result.slug}")

    def _fail(self, message, details=()):
        ctx = self.get_context_data(upload_error=message, upload_details=list(details))
        return self.render_to_response(ctx, status=400)


class ThemeActivateView(ThemeActionView):
    def perform(self, request, slug):
        services.activate(request, slug)
        theme = registry.get_theme(slug)
        messages.success(request, f"قالب «{theme.display_name}» فعال شد و هم‌اکنون روی سایت اعمال است.")


class ThemeDeactivateView(ThemeActionView):
    def perform(self, request, slug):
        services.deactivate(request, slug)
        messages.success(request, "قالب غیرفعال شد و قالب پیش‌فرض جایگزین آن شد.")


class ThemePreviewView(ThemeActionView):
    """قالب را فقط در پیش‌نویس انتخاب می‌کند و سایت را در حالت پیش‌نمایش باز می‌کند."""

    def perform(self, request, slug):
        services.set_draft_theme(request, slug)
        preview.start(request)
        return redirect("/")


# ------------------------------------------------------------------ پیش‌نمایش و انتشار
class PreviewStartView(ThemeActionView):
    def perform(self, request):
        preview.start(request)
        return redirect("/")


class PreviewStopView(ThemeActionView):
    success_message = "از حالت پیش‌نمایش خارج شدید."

    def perform(self, request):
        preview.stop(request)

    def get_redirect_url(self, request, **kwargs):
        return reverse("themes:customize")


class PublishView(ThemeActionView):
    success_message = "تغییرات ظاهر سایت منتشر شد و برای همه بازدیدکنندگان اعمال است."

    def perform(self, request):
        services.publish_draft(request)
        preview.stop(request)

    def get_redirect_url(self, request, **kwargs):
        return reverse("themes:customize")


class DiscardView(ThemeActionView):
    success_message = "تغییرات منتشرنشده دور ریخته شد."

    def perform(self, request):
        services.discard_draft(request)
        preview.stop(request)

    def get_redirect_url(self, request, **kwargs):
        return reverse("themes:customize")


# ------------------------------------------------------------------ ویرایشگر ظاهر
class CustomizeView(ThemesAdminMixin, TemplateView):
    """پایه صفحه‌های ویرایشگر: هر بخش فقط پیش‌نویس را ذخیره می‌کند، نه نسخه منتشرشده را."""

    tab = ""
    template_name = ""
    section_key = ""

    def get_cfg(self):
        return ThemeConfig.load()

    def get_doc(self):
        return services.working_doc(self.get_cfg())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cfg = self.get_cfg()
        doc = services.working_doc(cfg)
        theme = registry.get_theme(doc["theme"])
        ctx.update(
            tab=self.tab,
            cfg=cfg,
            doc=doc,
            working_theme=theme,
            preview_active=preview.is_active(self.request),
            supported_sections=[HOME_SECTION_TYPES[s] for s in (theme.home_sections if theme else ())],
        )
        return ctx

    def saved(self, request):
        messages.success(request, "تغییرات در پیش‌نویس ذخیره شد. برای اعمال روی سایت، آن را منتشر کنید.")
        return redirect(request.path)

    def invalid(self, request, ctx_updates):
        messages.error(request, "ذخیره انجام نشد. لطفاً خطاهای فرم را بررسی کنید.")
        ctx = self.get_context_data(**ctx_updates)
        return self.render_to_response(ctx)


class AppearanceView(CustomizeView):
    tab = "appearance"
    template_name = "themes/customize_appearance.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("form", AppearanceForm.from_doc(ctx["doc"]))
        ctx["site"] = SiteSettings.load()
        ctx["frame_url"] = reverse("themes:frame")
        return ctx

    def post(self, request, *args, **kwargs):
        form = AppearanceForm(request.POST)
        if form.is_valid():
            try:
                services.save_draft_part(request, "appearance", form.to_doc())
            except ThemeError as exc:
                messages.error(request, str(exc))
                return redirect(request.path)
            return self.saved(request)
        return self.invalid(request, {"form": form})


class LayoutView(CustomizeView):
    tab = "layout"
    template_name = "themes/customize_layout.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("form", LayoutForm.from_doc(ctx["doc"]))
        ctx["frame_url"] = reverse("themes:frame")
        return ctx

    def post(self, request, *args, **kwargs):
        form = LayoutForm(request.POST)
        if form.is_valid():
            header, footer = form.to_docs()
            services.save_draft_part(request, "header", header)
            services.save_draft_part(request, "footer", footer)
            return self.saved(request)
        return self.invalid(request, {"form": form})


class MenusView(CustomizeView):
    tab = "menus"
    template_name = "themes/customize_menus.html"

    def build_formsets(self, doc, data=None):
        return {
            location: MenuFormSet(data, initial=menu_initial(doc["menus"][location]) if data is None else None, prefix=location)
            for location in MENU_LOCATIONS
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        formsets = kwargs.get("formsets") or self.build_formsets(ctx["doc"])
        ctx["menu_formsets"] = [(MENU_LOCATIONS[loc], loc, fs) for loc, fs in formsets.items()]
        ctx["frame_url"] = reverse("themes:frame")
        return ctx

    def post(self, request, *args, **kwargs):
        formsets = self.build_formsets(None, data=request.POST)
        if all([fs.is_valid() for fs in formsets.values()]):
            services.save_draft_part(request, "menus", {loc: menu_from_formset(fs) for loc, fs in formsets.items()})
            return self.saved(request)
        return self.invalid(request, {"formsets": formsets})


class HomeSectionsView(CustomizeView):
    tab = "home"
    template_name = "themes/customize_home.html"

    def build_formset(self, doc, data=None):
        initial = section_initial(doc["home"]["sections"]) if data is None else None
        return SectionFormSet(data, initial=initial, prefix="sections")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["formset"] = kwargs.get("formset") or self.build_formset(ctx["doc"])
        ctx["frame_url"] = reverse("themes:frame")
        return ctx

    def post(self, request, *args, **kwargs):
        formset = self.build_formset(None, data=request.POST)
        if formset.is_valid():
            services.save_draft_part(request, "home", {"sections": sections_from_formset(formset)})
            return self.saved(request)
        return self.invalid(request, {"formset": formset})
