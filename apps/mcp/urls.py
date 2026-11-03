from django.urls import path, re_path

from . import endpoint, oauth, views

app_name = "mcp"

urlpatterns = [
    # the protocol endpoint (both spellings: a redirect would drop the POST body)
    path("mcp/", endpoint.endpoint, name="endpoint"),
    path("mcp", endpoint.endpoint),
    # discovery (RFC 9728 / RFC 8414); the optional suffix is the "path insertion" form some clients use
    path(".well-known/oauth-protected-resource", oauth.protected_resource, name="resource_metadata"),
    re_path(r"^\.well-known/oauth-protected-resource/(?P<suffix>.+)$", oauth.protected_resource),
    path(".well-known/oauth-authorization-server", oauth.authorization_server, name="server_metadata"),
    re_path(r"^\.well-known/oauth-authorization-server/(?P<suffix>.+)$", oauth.authorization_server),
    path("oauth/authorize", oauth.authorize, name="authorize"),
    path("oauth/token", oauth.token, name="token"),
    path("oauth/register", oauth.register, name="register"),
    path("oauth/revoke", oauth.revoke, name="revoke"),
    # panel + account pages
    path("panel/mcp/", views.SettingsView.as_view(), name="settings"),
    path("panel/mcp/connections/", views.AdminConnectionsView.as_view(), name="admin_connections"),
    path("panel/mcp/connections/<int:pk>/revoke/", views.AdminRevokeView.as_view(), name="admin_revoke"),
    path("accounts/connected-apps/", views.MyConnectionsView.as_view(), name="my_apps"),
    path("accounts/connected-apps/<int:pk>/revoke/", views.MyRevokeView.as_view(), name="my_revoke"),
]
