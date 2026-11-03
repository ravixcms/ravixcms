from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy

from . import scopes as sc

IDLE_DAYS = 90  # an unused connection dies after this long


class McpSettings(models.Model):
    """How Ravix MCP behaves on this site (a single row). Off until the superadmin turns it on."""

    class UnknownApps(models.TextChoices):
        WARN = "warn", gettext_lazy("Allow, with a red warning on the consent page")
        TRUSTED_ONLY = "trusted_only", gettext_lazy("Only apps from trusted addresses")

    enabled = models.BooleanField(gettext_lazy("Let AI assistants connect to this site"), default=False)
    admin_scopes = models.JSONField(null=True, blank=True, default=None)  # None = the built-in default for the role (scopes.DEFAULT_ROLE_SCOPES); a list, even an empty one, is the choice
    author_scopes = models.JSONField(null=True, blank=True, default=None)
    unknown_apps = models.CharField(gettext_lazy("Apps from addresses Ravix does not know"), max_length=20, choices=UnknownApps.choices, default=UnknownApps.WARN)
    trusted_hosts = models.TextField(gettext_lazy("Trusted addresses"), blank=True, help_text=gettext_lazy("One host name per line. claude.ai and claude.com are always trusted."))
    access_token_minutes = models.PositiveSmallIntegerField(gettext_lazy("Access token lifetime (minutes)"), default=30, validators=[MinValueValidator(5), MaxValueValidator(120)])
    rate_per_minute = models.PositiveSmallIntegerField(gettext_lazy("Calls per minute, per connection"), default=120, validators=[MinValueValidator(10), MaxValueValidator(2000)])
    write_per_minute = models.PositiveSmallIntegerField(gettext_lazy("Writes per minute, per connection"), default=30, validators=[MinValueValidator(1), MaxValueValidator(500)])
    allowed_origins = models.TextField(gettext_lazy("Extra allowed browser origins"), blank=True, help_text=gettext_lazy("One origin per line, for example https://app.example.com. Apps that are not web pages send none."))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Ravix MCP settings")
        verbose_name_plural = gettext_lazy("Ravix MCP settings")

    def __str__(self):
        return "Ravix MCP"

    @classmethod
    def load(cls) -> "McpSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    # ---- what is switched on
    @staticmethod
    def multisite_blocks() -> bool:
        return bool(getattr(settings, "RAVIX_MULTISITE_ENABLED", False))

    @property
    def is_active(self) -> bool:
        """On, and allowed to be on. Posts and pages are not per-site yet, so MCP stays off when several sites share one install."""
        return bool(self.enabled) and not self.multisite_blocks()

    def scopes_for_role(self, role: str) -> list[str]:
        from apps.accounts.permissions import Role

        if role == Role.SUPER_ADMIN:
            return list(sc.SCOPES)
        stored = {Role.ADMIN: self.admin_scopes, Role.AUTHOR: self.author_scopes}.get(role)
        if role not in (Role.ADMIN, Role.AUTHOR):
            return []
        if stored is None:
            return list(sc.DEFAULT_ROLE_SCOPES.get(role, ()))
        return [s for s in sc.SCOPES if s in set(stored)]

    def trusted_host_list(self) -> list[str]:
        return [h.strip().lower() for h in (self.trusted_hosts or "").splitlines() if h.strip()]

    def origin_list(self) -> list[str]:
        return [o.strip().rstrip("/").lower() for o in (self.allowed_origins or "").splitlines() if o.strip()]

    @property
    def access_ttl(self) -> timedelta:
        return timedelta(minutes=self.access_token_minutes)


class OAuthClient(models.Model):
    """An app that asked to connect. Either a Client ID Metadata Document (client_id is its https URL) or a dynamically registered client."""

    class Kind(models.TextChoices):
        CIMD = "cimd", "CIMD"
        DCR = "dcr", "DCR"

    client_id = models.CharField(max_length=500, unique=True)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    name = models.CharField(max_length=120)
    client_uri = models.CharField(max_length=500, blank=True)
    logo_uri = models.CharField(max_length=500, blank=True)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    fetched_at = models.DateTimeField(null=True, blank=True)  # CIMD: when the document was read

    def __str__(self):
        return f"{self.name} ({self.client_id})"

    def redirect_hosts(self) -> list[str]:
        from .clients import uri_host

        return sorted({uri_host(u) for u in self.redirect_uris if uri_host(u)})


class McpConnection(models.Model):
    """One person's authorisation of one app: what they agreed to. Revoking it kills every token that belongs to it."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_connections")
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="connections")
    scope = models.CharField(max_length=300)
    resource = models.CharField(max_length=500)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_reason = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def scope_list(self) -> list[str]:
        return sc.parse(self.scope)

    @property
    def active(self) -> bool:
        return self.revoked_at is None and not self.idle_expired

    @property
    def idle_expired(self) -> bool:
        return (self.last_used_at or self.created_at) < timezone.now() - timedelta(days=IDLE_DAYS)

    def revoke(self, reason: str = "revoked") -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.revoked_reason = reason[:40]
            self.save(update_fields=["revoked_at", "revoked_reason"])
            self.tokens.all().delete()


class McpToken(models.Model):
    """An access or refresh token. Only the SHA-256 of the token is stored."""

    ACCESS, REFRESH = "access", "refresh"

    connection = models.ForeignKey(McpConnection, on_delete=models.CASCADE, related_name="tokens")
    kind = models.CharField(max_length=8)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)  # a refresh token is used once; a second use means it leaked
    created_at = models.DateTimeField(default=timezone.now)


class AuthorizationCode(models.Model):
    """A one-time, 60-second code that the app trades for tokens. Bound to the app, redirect address, PKCE challenge and resource."""

    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    redirect_uri = models.CharField(max_length=2048)
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=300)
    resource = models.CharField(max_length=500)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    connection = models.ForeignKey(McpConnection, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
