from django.urls import path

from . import views

app_name = "media_library"

urlpatterns = [
    path("", views.MediaListView.as_view(), name="media_list"),
    path("upload/", views.MediaUploadView.as_view(), name="media_upload"),
    path("<int:pk>/", views.MediaUpdateView.as_view(), name="media_update"),
    path("<int:pk>/delete/", views.MediaDeleteView.as_view(), name="media_delete"),
]
