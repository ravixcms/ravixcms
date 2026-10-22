from django.urls import path

from . import api, views

app_name = "pluginhub"

urlpatterns = [
    # API عمومی
    path("registry/v1/", api.meta, name="api_meta"),
    path("registry/v1/categories/", api.categories, name="api_categories"),
    path("registry/v1/plugins/", api.plugin_list, name="api_plugins"),
    path("registry/v1/plugins/<slug:slug>/", api.plugin_detail, name="api_plugin"),
    path("registry/v1/plugins/<slug:slug>/versions/<str:version>/", api.version_detail, name="api_version"),
    path("registry/v1/plugins/<slug:slug>/versions/<str:version>/download/", api.download, name="api_download"),
    path("registry/v1/publish/", api.publish, name="api_publish"),
    # پنل
    path("panel/plugin-directory/", views.DirectoryView.as_view(), name="directory"),
    path("panel/plugin-directory/<slug:slug>/", views.DirectoryDetailView.as_view(), name="directory_detail"),
    path("panel/publish/", views.MyPluginsView.as_view(), name="my_plugins"),
    path("panel/publish/upload/", views.UploadView.as_view(), name="upload"),
    path("panel/publish/tokens/", views.TokensView.as_view(), name="tokens"),
    path("panel/publish/tokens/<int:pk>/revoke/", views.TokenRevokeView.as_view(), name="token_revoke"),
    path("panel/publish/<slug:slug>/", views.MyPluginView.as_view(), name="my_plugin"),
    path("panel/publish/<slug:slug>/<str:version>/<str:action>/", views.VersionActionView.as_view(), name="version_action"),
    path("panel/registry/publishers/", views.AdminPublishersView.as_view(), name="admin_publishers"),
    path("panel/registry/categories/", views.AdminCategoriesView.as_view(), name="admin_categories"),
    path("panel/registry/plugins/", views.AdminPluginsView.as_view(), name="admin_plugins"),
]
