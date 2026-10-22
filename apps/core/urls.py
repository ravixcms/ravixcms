from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("settings/", views.SiteSettingsView.as_view(), name="site_settings"),
]
