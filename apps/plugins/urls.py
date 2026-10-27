from django.urls import path, register_converter

from . import state, views


class _SiteRouteConverter:
    """Matches only paths some active plugin declared as a site route; everything else falls through to the next URL pattern."""

    regex = ".+"
    language_prefix = True

    def to_python(self, value):
        if state.find_site_route(value, self.language_prefix) is None:
            raise ValueError(value)
        return value

    def to_url(self, value):
        return value


class _LocalizedSiteRoute(_SiteRouteConverter):
    language_prefix = True


class _PlainSiteRoute(_SiteRouteConverter):
    language_prefix = False


register_converter(_LocalizedSiteRoute, "site_route")
register_converter(_PlainSiteRoute, "site_route_plain")

# Mounted by config/urls.py: the plain one at the site root, the localized one inside i18n_patterns (before the public site).
site_route_plain_patterns = [path("<site_route_plain:route_path>", views.site_route_plain, name="site_route_plain")]
site_route_localized_patterns = [path("<site_route:route_path>", views.site_route_localized, name="site_route")]

app_name = "plugins"

urlpatterns = [
    path("panel/plugins/", views.PluginListView.as_view(), name="list"),
    path("panel/plugins/events/", views.EventsView.as_view(), name="events"),
    path("panel/plugins/install-package/", views.InstallPackageView.as_view(), name="install_package"),
    path("panel/plugins/trusted-keys/", views.TrustedKeysView.as_view(), name="trusted_keys"),
    path("panel/modules/<slug:key>/toggle/", views.ModuleToggleView.as_view(), name="module_toggle"),
    path("panel/modules/<slug:key>/setup/", views.ModuleSetupView.as_view(), name="module_setup"),
    path("panel/plugins/x/<slug:slug>/<slug:key>/", views.admin_page, name="page"),
    path("panel/plugins/<slug:slug>/", views.PluginDetailView.as_view(), name="detail"),
    path("panel/plugins/<slug:slug>/install/", views.InstallView.as_view(), name="install"),
    path("panel/plugins/<slug:slug>/update/", views.UpdateView.as_view(), name="update"),
    path("panel/plugins/<slug:slug>/activate/", views.ActivateView.as_view(), name="activate"),
    path("panel/plugins/<slug:slug>/deactivate/", views.DeactivateView.as_view(), name="deactivate"),
    path("panel/plugins/<slug:slug>/upgrade/", views.UpgradeView.as_view(), name="upgrade"),
    path("panel/plugins/<slug:slug>/rollback/<int:pk>/", views.RollbackView.as_view(), name="rollback"),
    path("panel/plugins/<slug:slug>/uninstall/", views.UninstallView.as_view(), name="uninstall"),
    path("panel/plugins/<slug:slug>/settings/", views.SettingsView.as_view(), name="settings"),
    path("ext/<slug:slug>/", views.public_route, name="public_root"),
    path("ext/<slug:slug>/<path:path>", views.public_route, name="public"),
]
