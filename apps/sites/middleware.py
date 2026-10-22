from django.utils.functional import SimpleLazyObject

from .access import default_site, multisite_enabled
from .models import SiteDomain


def _resolve(request):
    if not multisite_enabled():
        return default_site()
    host = request.get_host().split(":")[0].lower()  # get_host اعتبارسنجی ALLOWED_HOSTS را هم انجام می‌دهد
    d = SiteDomain.objects.select_related("site").filter(domain__in=[host, request.get_host().lower()], site__is_active=True).first()
    return d.site if d else None


class CurrentSiteMiddleware:
    """request.site: سایت عمومی براساس دامنه. تنبل است تا صفحاتی که نیازی ندارند کوئری اضافه نزنند. None = دامنهٔ ناشناخته."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.site = SimpleLazyObject(lambda: _resolve(request))
        return self.get_response(request)


def current_site(request):
    """مقدار واقعی یا None (SimpleLazyObject برای None را به این شکل امن باز می‌کند)."""
    site = _resolve(request) if not hasattr(request, "site") else request.site
    try:
        return site if getattr(site, "pk", None) else None
    except Exception:  # noqa: BLE001
        return None
