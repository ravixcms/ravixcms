from django.utils.translation import gettext, gettext_lazy
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

import re

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(:\d{1,5})?$")


class Site(models.Model):
    """یک سایت مستقل. دادهٔ هر سایت (محصول، سفارش، ...) با کلید خارجی به همین مدل جدا می‌شود."""

    name = models.CharField(gettext_lazy("Site name"), max_length=100)
    slug = models.SlugField(gettext_lazy("Identifier"), max_length=50, unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_sites", verbose_name=gettext_lazy("Owner"))
    is_default = models.BooleanField(gettext_lazy("Default site"), default=False)
    is_active = models.BooleanField(gettext_lazy("Active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = gettext_lazy("Site")
        verbose_name_plural = gettext_lazy("Sites")
        constraints = [models.UniqueConstraint(fields=["is_default"], condition=Q(is_default=True), name="sites_single_default")]

    def __str__(self):
        return self.name


class SiteDomain(models.Model):
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="domains")
    domain = models.CharField(gettext_lazy("Domain"), max_length=253, unique=True)
    is_primary = models.BooleanField(gettext_lazy("Primary"), default=False)

    class Meta:
        verbose_name = gettext_lazy("Domain")
        verbose_name_plural = gettext_lazy("Domains")

    def clean(self):
        self.domain = (self.domain or "").strip().lower()
        if not DOMAIN_RE.match(self.domain):
            raise ValidationError({"domain": gettext("Invalid domain.")})

    def save(self, *args, **kwargs):
        self.domain = (self.domain or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.domain


class SiteMember(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", gettext_lazy("Owner")
        MANAGER = "manager", gettext_lazy("Manager")
        STAFF = "staff", gettext_lazy("Staff")

    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="site_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["site", "user"], name="sites_unique_member")]
        verbose_name = gettext_lazy("Site member")
        verbose_name_plural = gettext_lazy("Site members")
