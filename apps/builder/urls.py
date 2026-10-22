from django.urls import path

from . import views

app_name = "builder"

urlpatterns = [
    path("", views.PageListView.as_view(), name="list"),
    path("media/", views.MediaListApi.as_view(), name="api_media"),
    path("<int:page_id>/", views.EditorView.as_view(), name="editor"),
    path("<int:page_id>/preview/", views.PreviewView.as_view(), name="preview"),
    path("<int:page_id>/api/state/", views.StateApi.as_view(), name="api_state"),
    path("<int:page_id>/api/draft/", views.DraftApi.as_view(), name="api_draft"),
    path("<int:page_id>/api/versions/", views.VersionsApi.as_view(), name="api_versions"),
    path("<int:page_id>/api/versions/<int:number>/", views.VersionActionApi.as_view(), name="api_version"),
]
