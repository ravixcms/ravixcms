"""
قواعد دسترسی به سایت.

دو لایه، هر دو لازم‌اند: (۱) قابلیت سراسری نقش (مثل shop.orders) و (۲) دسترسی به همان سایت.
دسترسی به سایت: مدیرکل همه‌جا؛ در حالت تک‌سایتی (پیش‌فرض) هر کاربر دارای قابلیت به سایت پیش‌فرض؛
در حالت چندسایتی فقط اعضای همان سایت (یا مالک آن).
"""
from django.utils.translation import gettext
from django.conf import settings

from apps.accounts.permissions import Role

from .models import Site, SiteMember


def default_site() -> Site:
    site = Site.objects.filter(is_default=True).first()
    if site is None:  # پایگاه‌دادهٔ تازه/تست
        site, _ = Site.objects.get_or_create(slug="main", defaults={"name": gettext("Main site"), "is_default": True})
    return site


def multisite_enabled() -> bool:
    return bool(getattr(settings, "RAVIX_MULTISITE_ENABLED", False))


def can_access_site(user, site, *, roles=None) -> bool:
    if site is None or not getattr(user, "is_authenticated", False) or not user.is_active or not site.is_active:
        return False
    if user.role == Role.SUPER_ADMIN:
        return True
    if not multisite_enabled():
        return bool(site.is_default)
    if site.owner_id == user.pk:
        return True
    qs = SiteMember.objects.filter(site=site, user=user)
    if roles:
        qs = qs.filter(role__in=roles)
    return qs.exists()


def accessible_sites(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Site.objects.none()
    if user.role == Role.SUPER_ADMIN:
        return Site.objects.filter(is_active=True)
    if not multisite_enabled():
        return Site.objects.filter(is_default=True, is_active=True)
    return Site.objects.filter(is_active=True).filter(models_q(user)).distinct()


def models_q(user):
    from django.db.models import Q

    return Q(owner=user) | Q(members__user=user)


SESSION_KEY = "ravix_active_site"


def panel_site(request):
    """سایت فعال پنل برای کاربر؛ همیشه با دسترسی واقعی بازبینی می‌شود (مقدار نشست قابل اعتماد نیست). None اگر هیچ سایتی ندارد."""
    sites = accessible_sites(request.user)
    wanted = request.session.get(SESSION_KEY)
    if wanted:
        site = sites.filter(pk=wanted).first()
        if site:
            return site
    site = sites.order_by("-is_default", "name").first()
    return site


def create_site(name, slug, owner):
    site = Site.objects.create(name=name, slug=slug, owner=owner)
    SiteMember.objects.create(site=site, user=owner, role=SiteMember.Role.OWNER)
    return site
