from django.urls import path

from . import views

app_name = "sites"

urlpatterns = [
    path("sites/", views.SiteListView.as_view(), name="list"),
    path("sites/switch/", views.SwitchSiteView.as_view(), name="switch"),
    path("sites/<int:pk>/", views.SiteDetailView.as_view(), name="detail"),
]
