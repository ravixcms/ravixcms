from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse

from .models import SiteSettings
from .navigation import SECTIONS, get_nav_items


def site_settings(request):
    """تنظیمات سایت و منوی کناری را در همه قالب‌ها در دسترس می‌گذارد."""
    try:
        site = SiteSettings.load()
    except (OperationalError, ProgrammingError):  # پیش از اجرای migrate
        site = SiteSettings()

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
    return {"site": site, "sidebar": sidebar, "panel_sites": panel_sites if len(panel_sites) > 1 else [], "active_site": active_site}
