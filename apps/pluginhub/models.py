from django.utils.translation import gettext_lazy
import hashlib
import secrets

from django.conf import settings
from django.db import models

from .storage import package_storage


class Publisher(models.Model):
    """ناشر مجاز. فقط کاربری که رکورد فعال Publisher دارد می‌تواند افزونه منتشر کند؛ اعطا/لغو فقط توسط مدیرکل."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="publisher")
    display_name = models.CharField(gettext_lazy("Publisher name"), max_length=80)
    website = models.URLField(gettext_lazy("Website"), blank=True)
    is_active = models.BooleanField(gettext_lazy("Active"), default=True)
    is_verified = models.BooleanField(gettext_lazy("Verified"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = gettext_lazy("Publisher")
        verbose_name_plural = gettext_lazy("Publishers")
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name


class Category(models.Model):
    slug = models.SlugField(gettext_lazy("Slug"), max_length=30, unique=True)
    name = models.CharField(gettext_lazy("Name"), max_length=60)
    order = models.PositiveSmallIntegerField(gettext_lazy("Sort order"), default=100)

    class Meta:
        verbose_name = gettext_lazy("Category")
        verbose_name_plural = gettext_lazy("Categories")
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class HubPlugin(models.Model):
    """افزونه در مخزن. ``slug`` همان شناسهٔ یکتای manifest است و پس از ثبت، متعلق به همان ناشر می‌ماند (جلوگیری از ربودن نام)."""

    slug = models.SlugField(gettext_lazy("Unique identifier"), max_length=40, unique=True)
    name = models.CharField(gettext_lazy("Name"), max_length=80)
    summary = models.CharField(gettext_lazy("Summary"), max_length=300, blank=True)
    description = models.TextField(gettext_lazy("Description"), blank=True, max_length=10000)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="plugins")
    publisher = models.ForeignKey(Publisher, on_delete=models.PROTECT, related_name="plugins")
    homepage = models.URLField(gettext_lazy("Plugin page"), blank=True)
    license = models.CharField(gettext_lazy("License"), max_length=40, blank=True)
    is_listed = models.BooleanField(gettext_lazy("Show in listing"), default=True)  # مدیر مخزن می‌تواند از فهرست خارج کند
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Registry plugin")
        verbose_name_plural = gettext_lazy("Registry plugins")
        ordering = ["name"]

    def __str__(self):
        return self.name


def _package_path(instance, filename):
    return f"{instance.plugin.slug}/{instance.plugin.slug}-{instance.version}.zip"


class HubVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", gettext_lazy("Draft")
        PUBLISHED = "published", gettext_lazy("Published")
        YANKED = "yanked", gettext_lazy("Withdrawn")

    plugin = models.ForeignKey(HubPlugin, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(gettext_lazy("Version"), max_length=20)
    status = models.CharField(gettext_lazy("Status"), max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    manifest = models.JSONField("manifest", default=dict)
    package = models.FileField(gettext_lazy("Package"), upload_to=_package_path, storage=package_storage, max_length=200)
    sha256 = models.CharField(gettext_lazy("Package SHA-256"), max_length=64)
    fingerprint = models.CharField(gettext_lazy("Content fingerprint"), max_length=64, help_text=gettext_lazy("The same fingerprint that the plugins panel shows during installation."))
    size = models.PositiveIntegerField(gettext_lazy("Size (bytes)"))
    ravin_spec = models.CharField(gettext_lazy("Compatibility with Ravix"), max_length=60)
    api_version = models.PositiveSmallIntegerField(gettext_lazy("Plugin contract version"))
    signature = models.CharField(gettext_lazy("Ed25519 signature (base64)"), max_length=120, blank=True)
    signature_key_id = models.CharField(gettext_lazy("Signing key ID"), max_length=16, blank=True)
    changelog = models.TextField(gettext_lazy("Changes"), blank=True, max_length=5000)
    yank_reason = models.CharField(gettext_lazy("Reason for withdrawal"), max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    downloads = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = gettext_lazy("Plugin version")
        verbose_name_plural = gettext_lazy("Plugin versions")
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["plugin", "version"], name="uniq_hub_plugin_version")]

    def __str__(self):
        return f"{self.plugin.slug} {self.version}"

    @property
    def parsed(self):
        from apps.plugins.versions import parse
        return parse(self.version) or (0, 0, 0)


class PublisherToken(models.Model):
    """توکن API ناشر. فقط SHA-256 توکن ذخیره می‌شود؛ مقدار اصلی یک‌بار هنگام ساخت نمایش داده می‌شود و قابل بازیابی نیست."""

    publisher = models.ForeignKey(Publisher, on_delete=models.CASCADE, related_name="tokens")
    name = models.CharField(gettext_lazy("Name"), max_length=60)
    prefix = models.CharField(max_length=12, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, publisher, name):
        raw = "rvn_" + secrets.token_urlsafe(32)
        obj = cls.objects.create(publisher=publisher, name=name[:60], prefix=raw[:12], token_hash=cls.hash(raw))
        return obj, raw
