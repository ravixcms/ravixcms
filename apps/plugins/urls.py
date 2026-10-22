from django.urls import path

from . import views

app_name = "plugins"

urlpatterns = [
    path("panel/plugins/", views.PluginListView.as_view(), name="list"),
    path("panel/plugins/events/", views.EventsView.as_view(), name="events"),
    path("panel/plugins/install-package/", views.InstallPackageView.as_view(), name="install_package"),
    path("panel/plugins/trusted-keys/", views.TrustedKeysView.as_view(), name="trusted_keys"),
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
