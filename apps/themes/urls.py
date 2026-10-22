from django.urls import path

from . import views
from apps.public.views import PreviewFrameView

app_name = "themes"

urlpatterns = [
    path("themes/", views.ThemeListView.as_view(), name="list"),
    path("themes/upload/", views.ThemeUploadView.as_view(), name="upload"),
    path("themes/<str:slug>/activate/", views.ThemeActivateView.as_view(), name="activate"),
    path("themes/<str:slug>/deactivate/", views.ThemeDeactivateView.as_view(), name="deactivate"),
    path("themes/<str:slug>/preview/", views.ThemePreviewView.as_view(), name="preview_theme"),
    path("appearance/", views.AppearanceView.as_view(), name="customize"),
    path("appearance/layout/", views.LayoutView.as_view(), name="layout"),
    path("appearance/menus/", views.MenusView.as_view(), name="menus"),
    path("appearance/home/", views.HomeSectionsView.as_view(), name="home"),
    path("appearance/frame/", PreviewFrameView.as_view(), name="frame"),
    path("appearance/preview/start/", views.PreviewStartView.as_view(), name="preview_start"),
    path("appearance/preview/stop/", views.PreviewStopView.as_view(), name="preview_stop"),
    path("appearance/publish/", views.PublishView.as_view(), name="publish"),
    path("appearance/discard/", views.DiscardView.as_view(), name="discard"),
]
