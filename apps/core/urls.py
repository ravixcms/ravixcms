from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("settings/", views.SiteSettingsView.as_view(), name="site_settings"),
    path("site-health/", views.SiteHealthView.as_view(), name="site_health"),
    path("site-health/test-email/", views.SiteHealthTestEmailView.as_view(), name="site_health_test_email"),
]
