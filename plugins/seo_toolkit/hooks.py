from .models import SeoMeta


def _fill(pattern, title, site):
    return pattern.replace("{title}", title).replace("{site}", site)


def apply_seo(seo, api, *, kind, obj_id, site, request, **_):
    """hook فیلتر public.seo: مقادیر سئوی هر صفحه/نوشته را روی خروجی هسته اعمال می‌کند (خروجی را هسته دوباره پاک‌سازی می‌کند)."""
    out = {}
    meta = SeoMeta.objects.filter(kind=kind, object_id=obj_id).first()
    pattern = api.setting("title_pattern", "{title} | {site}") or "{title} | {site}"
    if meta and meta.title:
        out["title"] = _fill(pattern, meta.title, site["name"])
    if meta and meta.description:
        out["description"] = meta.description
    elif not seo.get("description") and api.setting("fallback_description"):
        out["description"] = api.setting("fallback_description")
    if meta and meta.noindex:
        out["robots"] = "noindex,follow"
    elif api.setting("default_robots") and not seo.get("robots"):
        out["robots"] = api.setting("default_robots")
    if meta and meta.canonical:
        out["canonical"] = meta.canonical
    if meta and meta.og_image:
        out["og_image"] = meta.og_image
    return out
