from django.urls import path

from . import views

app_name = "studio"

urlpatterns = [
    path("", views.StudioListView.as_view(), name="list"),
    path("media/", views.MediaListApi.as_view(), name="api_media"),
    path("<str:kind>/<int:pk>/", views.EditorView.as_view(), name="editor"),
    path("d/<int:doc_id>/preview/", views.PreviewView.as_view(), name="preview"),
    path("d/<int:doc_id>/api/state/", views.StateApi.as_view(), name="api_state"),
    path("d/<int:doc_id>/api/ping/", views.PingApi.as_view(), name="api_ping"),
    path("d/<int:doc_id>/api/draft/", views.DraftApi.as_view(), name="api_draft"),
    path("d/<int:doc_id>/api/render/", views.RenderApi.as_view(), name="api_render"),
    path("d/<int:doc_id>/api/versions/", views.VersionsApi.as_view(), name="api_versions"),
    path("d/<int:doc_id>/api/versions/<int:number>/", views.VersionActionApi.as_view(), name="api_version"),
    path("d/<int:doc_id>/api/export/", views.ExportApi.as_view(), name="api_export"),
    path("d/<int:doc_id>/api/import/", views.ImportApi.as_view(), name="api_import"),
]
