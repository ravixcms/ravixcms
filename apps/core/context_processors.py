from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse

from django.conf import settings

from .models import SiteSettings
from .version import RAVIX_VERSION
from .navigation import SECTIONS, get_nav_items


def official_links() -> dict:
    """Project website / docs / repository for footers and the installer. Only http(s) addresses are passed on to templates."""
    out = {}
    for key, name in (("website", "RAVIX_WEBSITE_URL"), ("docs", "RAVIX_DOCS_URL"), ("repo", "RAVIX_REPO_URL")):
        value = str(getattr(settings, name, "") or "")
        out[key] = value if value.startswith(("https://", "http://")) else ""
    return out


def site_settings(request):
    """تنظیمات سایت و منوی کناری را در همه قالب‌ها در دسترس می‌گذارد."""
    site = getattr(request, "site_settings", None)  # SiteRegionalMiddleware همین درخواست خوانده است
    if site is None:
        try:
            site = SiteSettings.load()
        except (OperationalError, ProgrammingError):  # پیش از اجرای migrate
            site = SiteSettings()

    site = site.localized_copy()  # name, tagline, description and footer text in the active language
    sidebar = []
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        current_view = getattr(getattr(request, "resolver_match", None), "view_name", "")
        sections: dict[str, list] = {}
        items = list(get_nav_items())
        try:  # منوی افزونه‌ها: خطای افزونه هرگز نباید پنل را از کار بیندازد
            from apps.plugins.state import nav_items

            items += nav_items()
            items.sort(key=lambda i: (list(SECTIONS).index(i.section), i.order))
        except Exception:  # noqa: BLE001
            pass
        for item in items:
            if item.capability and not user.has_cap(item.capability):
                continue
            sections.setdefault(item.section, []).append(
                {
                    "label": item.label,
                    "icon": item.icon,
                    "url": reverse(item.url_name) if item.url_name else item.url,
                    "active": current_view in item.active_urls or bool(item.url and request.path.startswith(item.url)),
                }
            )
        sidebar = [{"title": SECTIONS[key], "items": items} for key, items in sections.items()]
    panel_sites, active_site = [], None
    if user is not None and user.is_authenticated:
        try:  # فقط در حالت چندسایتی و فقط اگر بیش از یک سایت دارد
            from apps.sites.access import accessible_sites, multisite_enabled, panel_site

            if multisite_enabled():
                panel_sites = list(accessible_sites(user))
                active_site = panel_site(request)
        except Exception:  # noqa: BLE001
            pass
    floating_actions = []
    if user is not None and user.is_authenticated and request.path_info.startswith("/panel/") and request.GET.get("embed") != "1":
        try:  # buttons that active plugins add to every panel page; a plugin error must never break the panel
            from apps.plugins.hooks import apply_filter

            floating_actions = apply_filter("panel.floating_actions", [], request=request)
        except Exception:  # noqa: BLE001
            floating_actions = []
    return {"site": site, "floating_actions": floating_actions, "sidebar": sidebar, "panel_sites": panel_sites if len(panel_sites) > 1 else [], "active_site": active_site,
            "ravix_version": RAVIX_VERSION, "ravix_links": official_links()}
