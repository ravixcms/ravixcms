from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

import re

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(:\d{1,5})?$")


class Site(models.Model):
    """یک سایت مستقل. دادهٔ هر سایت (محصول، سفارش، ...) با کلید خارجی به همین مدل جدا می‌شود."""

    name = models.CharField("نام سایت", max_length=100)
    slug = models.SlugField("شناسه", max_length=50, unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_sites", verbose_name="مالک")
    is_default = models.BooleanField("سایت پیش‌فرض", default=False)
    is_active = models.BooleanField("فعال", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سایت"
        verbose_name_plural = "سایت‌ها"
        constraints = [models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True), name="sites_single_default")]

    def __str__(self):
        return self.name


class SiteDomain(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="domains")
    domain = models.CharField("دامنه", max_length=253, unique=True)
    is_primary = models.BooleanField("اصلی", default=False)

    class Meta:
        verbose_name = "دامنه"
        verbose_name_plural = "دامنه‌ها"

    def clean(self):
        self.domain = (self.domain or "").strip().lower()
        if not DOMAIN_RE.match(self.domain):
            raise ValidationError({"domain": "دامنهٔ نامعتبر."})

    def save(self, *args, **kwargs):
        self.domain = (self.domain or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.domain


class SiteMember(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "مالک"
        MANAGER = "manager", "مدیر"
        STAFF = "staff", "کارمند"

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="site_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "user"], name="sites_unique_member")]
        verbose_name = "عضو سایت"
        verbose_name_plural = "اعضای سایت"
