from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("settings/", views.SiteSettingsView.as_view(), name="site_settings"),
    path("speed/", views.SpeedSettingsView.as_view(), name="speed_settings"),
    path("email/", views.EmailSettingsView.as_view(), name="email_settings"),
    path("site-health/", views.SiteHealthView.as_view(), name="site_health"),
    path("site-health/test-email/", views.SiteHealthTestEmailView.as_view(), name="site_health_test_email"),
]
