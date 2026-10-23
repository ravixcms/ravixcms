"""Language-neutral public endpoints that must live at the site root (never under a /<lang>/ prefix)."""
from django.urls import path

from . import views

app_name = "seo"

urlpatterns = [
    path("sitemap.xml", views.SitemapIndexView.as_view(), name="index"),
    path("robots.txt", views.RobotsView.as_view(), name="robots"),
]
