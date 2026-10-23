"""منطق مخزن: ناشر مجاز، آپلود/انتشار نسخه، سازگاری، جست‌وجو. همهٔ بررسی‌های دسترسی اینجا (و در لایهٔ view) انجام می‌شود."""
from django.utils.translation import gettext
import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.version import PLUGIN_API_VERSION, RAVIX_VERSION
from apps.plugins import versions

from .models import Category, HubPlugin, HubVersion, Publisher, PublisherToken
from .packages import Inspected, PackageError, inspect


class HubError(Exception):
    pass


# ------------------------------------------------------------------ ناشر
def get_publisher(user) -> Publisher | None:
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return Publisher.objects.filter(user=user, is_active=True).first()


def authenticate_token(raw: str) -> Publisher | None:
    if not raw or len(raw) > 100:
        return None
    tok = PublisherToken.objects.select_related("publisher", "publisher__user").filter(token_hash=PublisherToken.hash(raw), revoked_at__isnull=True).first()
    if tok is None or not tok.publisher.is_active or not tok.publisher.user.is_active:
        return None
    PublisherToken.objects.filter(pk=tok.pk).update(last_used_at=timezone.now())
    return tok.publisher


# ------------------------------------------------------------------ سازگاری
@dataclass
class Compat:
    ok: bool
    problems: list

    def as_dict(self):
        return {"compatible": self.ok, "problems": self.problems, "ravix_version": RAVIX_VERSION, "ravin_version": RAVIX_VERSION, "plugin_api_version": PLUGIN_API_VERSION}


def compat_for(api_version: int, ravin_spec: str) -> Compat:
    problems = []
    if api_version != PLUGIN_API_VERSION:
        problems.append((gettext("The plugin contract is %(api_version)s; this CMS supports contract %(PLUGIN_API_VERSION)s.") % {"api_version": api_version, "PLUGIN_API_VERSION": PLUGIN_API_VERSION}))
    if not versions.satisfies(RAVIX_VERSION, ravin_spec):
        problems.append((gettext("Requires Ravix “%(ravin_spec)s”; current version is %(RAVIX_VERSION)s.") % {"ravin_spec": ravin_spec, "RAVIX_VERSION": RAVIX_VERSION}))
    return Compat(not problems, problems)


def version_compat(v: HubVersion) -> Compat:
    return compat_for(v.api_version, v.ravin_spec)


def latest_published(plugin: HubPlugin, *, compatible_only=False) -> HubVersion | None:
    cands = [v for v in plugin.versions.all() if v.status == HubVersion.Status.PUBLISHED]
    if compatible_only:
        cands = [v for v in cands if version_compat(v).ok]
    return max(cands, key=lambda v: v.parsed, default=None)


# ------------------------------------------------------------------ جست‌وجو
def search(q="", category="", compatible=False):
    qs = HubPlugin.objects.filter(is_listed=True, publisher__is_active=True, versions__status=HubVersion.Status.PUBLISHED).select_related("category", "publisher").distinct()
    q = (q or "").strip()[:100]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q) | Q(summary__icontains=q) | Q(description__icontains=q))
    if category:
        qs = qs.filter(category__slug=category)
    items = list(qs.prefetch_related("versions").order_by("name"))
    out = []
    for p in items:
        latest = latest_published(p)
        compat_latest = latest_published(p, compatible_only=True)
        if compatible and compat_latest is None:
            continue
        out.append((p, latest, compat_latest))
    return out


# ------------------------------------------------------------------ آپلود و چرخهٔ نسخه
def _check_ownership(plugin, publisher):
    if plugin.publisher_id != publisher.pk:
        raise HubError(gettext("This identifier belongs to another publisher."))


@transaction.atomic
def upload_version(publisher: Publisher, user, data: bytes, *, changelog="", publish=False) -> HubVersion:
    if publisher is None or not publisher.is_active:
        raise HubError(gettext("Only authorized publishers can publish plugins."))
    if not settings.RAVIX_REGISTRY_ENABLED:
        raise HubError(gettext("The registry is disabled."))
    try:
        info: Inspected = inspect(data)
    except PackageError as exc:
        raise HubError(str(exc))
    m = info.manifest
    plugin = HubPlugin.objects.select_for_update().filter(slug=m.slug).first()
    if plugin is None:
        cat = Category.objects.filter(slug=m.category).first() if m.category else None
        plugin = HubPlugin.objects.create(slug=m.slug, name=m.name, summary=m.description[:300], description=m.description, category=cat,
                                          publisher=publisher, homepage=m.homepage, license=m.license)
    else:
        _check_ownership(plugin, publisher)
    if HubVersion.objects.filter(plugin=plugin, version=m.version).exists():
        raise HubError((gettext("Version %(version)s is already registered; versions are immutable. Create a new version.") % {"version": m.version}))
    ver = HubVersion(plugin=plugin, version=m.version, manifest=info.raw_manifest, sha256=info.sha256, fingerprint=info.fingerprint, size=info.size,
                     ravin_spec=m.ravix, api_version=m.api_version, changelog=(changelog or "")[:5000], uploaded_by=user)
    ver.package.save(f"{m.slug}-{m.version}.zip", ContentFile(info.data), save=False)
    try:
        ver.save()
    except IntegrityError:
        raise HubError(gettext("Duplicate version."))
    if publish:
        publish_version(ver, publisher)
    return ver


def publish_version(ver: HubVersion, publisher: Publisher, *, is_admin=False):
    if not is_admin:
        _check_ownership(ver.plugin, publisher)
        if not publisher.is_active:
            raise HubError(gettext("Only authorized publishers can publish plugins."))
    if ver.status == HubVersion.Status.PUBLISHED:
        return ver
    if ver.status == HubVersion.Status.YANKED:
        raise HubError(gettext("A withdrawn version cannot be published again; create a new version."))
    # بازبینی تمامیت فایل ذخیره‌شده پیش از انتشار
    ver.package.open("rb")
    try:
        actual = hashlib.sha256(ver.package.read()).hexdigest()
    finally:
        ver.package.close()
    if actual != ver.sha256:
        raise HubError(gettext("The integrity of the stored file could not be verified (SHA-256 mismatch)."))
    sign_version(ver)
    ver.status = HubVersion.Status.PUBLISHED
    ver.published_at = timezone.now()
    ver.save(update_fields=["status", "published_at", "signature", "signature_key_id"])
    return ver


def sign_version(ver: HubVersion) -> bool:
    """نسخه را با کلید امضای مخزن (فایل/متغیر محیطی؛ هرگز در کد) امضا می‌کند. بدون کلید: بدون امضا می‌ماند (نصب‌کننده آن را رد می‌کند)."""
    from apps.plugins import signing

    try:
        seed = signing.registry_signing_seed()
        if not seed:
            return False
        ver.signature, ver.signature_key_id = signing.sign(seed, ver.plugin.slug, ver.version, ver.sha256, ver.fingerprint)
    except (signing.SigningError, ValueError) as exc:
        raise HubError((gettext("Signing the version failed (check the registry signing key): %(exc)s") % {"exc": exc}))
    return True


def yank_version(ver: HubVersion, publisher: Publisher | None, reason="", *, is_admin=False):
    if not is_admin:
        _check_ownership(ver.plugin, publisher)
    ver.status = HubVersion.Status.YANKED
    ver.yank_reason = (reason or "")[:200]
    ver.save(update_fields=["status", "yank_reason"])
    return ver


def delete_draft(ver: HubVersion, publisher: Publisher):
    _check_ownership(ver.plugin, publisher)
    if ver.status != HubVersion.Status.DRAFT:
        raise HubError(gettext("Only drafts can be deleted; “withdraw” a published version instead."))
    ver.package.delete(save=False)
    ver.delete()
