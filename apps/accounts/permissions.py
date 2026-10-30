"""
سیستم نقش‌ها و دسترسی‌ها.

هر نقش مجموعه‌ای از «قابلیت‌ها» (capability) دارد. ماژول‌های آینده فقط کافی است
قابلیت‌های جدید را اینجا اضافه کنند و با ``user.has_cap("...")`` یا
``CapabilityRequiredMixin`` از آن‌ها استفاده کنند.
"""
from django.utils.translation import gettext_lazy
from django.db import models


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", gettext_lazy("Superadmin")
    ADMIN = "admin", gettext_lazy("Manager")
    AUTHOR = "author", gettext_lazy("Author")
    USER = "user", gettext_lazy("Regular user")


ROLE_LEVEL = {
    Role.USER: 10,
    Role.AUTHOR: 20,
    Role.ADMIN: 30,
    Role.SUPER_ADMIN: 40,
}

CAPABILITIES = {
    "dashboard.view": gettext_lazy("View the dashboard"),
    "dashboard.stats": gettext_lazy("View site-wide statistics"),
    "profile.manage": gettext_lazy("Manage own profile"),
    "users.view": gettext_lazy("View the user list"),
    "users.manage": gettext_lazy("Manage user roles and status"),
    "settings.manage": gettext_lazy("Manage site settings"),
    "health.view": gettext_lazy("View “Site health” and send a test email"),
    # قابلیت‌های آماده برای مرحله‌های بعد (ماژول محتوا)
    "content.write": gettext_lazy("Write content"),
    "content.publish": gettext_lazy("Publish and manage everyone’s posts"),
    "pages.manage": gettext_lazy("Manage static pages"),
    "taxonomy.manage": gettext_lazy("Manage categories and tags"),
    "redirects.manage": gettext_lazy("Manage redirects (moving old URLs)"),
    "comments.moderate": gettext_lazy("Manage and approve comments"),
    "media.upload": gettext_lazy("Upload and view the media library"),
    "media.manage": gettext_lazy("Edit and delete everyone’s media"),
    "audit.view": gettext_lazy("View the admin event log"),
    "themes.manage": gettext_lazy("Manage the theme and site appearance"),
    "themes.install": gettext_lazy("Upload and install a theme from a ZIP file (superadmin only)"),
    "studio.edit": gettext_lazy("Design pages and posts with Ravix Studio, the visual page builder"),
    "studio.publish": gettext_lazy("Approve and publish Ravix Studio layouts"),
    "studio.html": gettext_lazy("Use the custom HTML widget in Ravix Studio (administrators only)"),
    "plugins.manage": gettext_lazy("Manage plugins (install, activate, settings)"),
    "modules.manage": gettext_lazy("Switch built-in modules such as Ravix Shop on and off (superadmin only)"),
    "plugins.trust": gettext_lazy("Manage trusted plugin signing keys (root of trust; superadmin only)"),
    "registry.manage": gettext_lazy("Manage the plugin registry: publishers, categories, delisting (superadmin only)"),
    "sites.manage": gettext_lazy("Manage sites, domains and members (superadmin only)"),
    "shop.manage": gettext_lazy("Manage shop products"),
    "shop.orders": gettext_lazy("View and process shop orders"),
    "shop.gateways": gettext_lazy("Manage shop payment gateways (enable/disable and connection keys)"),
    "updates.view": gettext_lazy("View versions, available updates and the upgrade history"),
    "updates.manage": gettext_lazy("Apply/roll back core and theme updates (superadmin only; replaces the application code)"),
}

_BASIC = {"dashboard.view", "profile.manage"}
_ADMIN = _BASIC | {
    "dashboard.stats",
    "users.view",
    "users.manage",
    "settings.manage",
    "health.view",
    "content.write",
    "content.publish",
    "pages.manage",
    "taxonomy.manage",
    "redirects.manage",
    "comments.moderate",
    "media.upload",
    "media.manage",
    "audit.view",
    "themes.manage",
    "studio.edit",
    "studio.publish",
    "studio.html",
    "plugins.manage",
    "shop.manage",
    "shop.orders",
    "shop.gateways",
    "updates.view",
}

ROLE_CAPABILITIES = {
    Role.USER: frozenset({"profile.manage"}),  # a member: their own account page only, nothing of the panel (no dashboard.view)
    Role.AUTHOR: frozenset(_BASIC | {"content.write", "media.upload", "studio.edit"}),
    Role.ADMIN: frozenset(_ADMIN),
    Role.SUPER_ADMIN: frozenset(CAPABILITIES),
}
