from django.urls import path

from . import views

app_name = "comments"
urlpatterns = [
    path("comments/", views.CommentListView.as_view(), name="list"),
    path("comments/bulk/", views.CommentBulkView.as_view(), name="bulk"),
]
