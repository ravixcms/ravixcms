from django.urls import path

from . import views

app_name = "updater"

urlpatterns = [
    path("updates/", views.HomeView.as_view(), name="home"),
    path("updates/guide/", views.GuideView.as_view(), name="guide"),
    path("updates/check/", views.CheckView.as_view(), name="check"),
    path("updates/run/<int:pk>/", views.RunDetailView.as_view(), name="run"),
    path("updates/run/<int:pk>/rollback/", views.RollbackView.as_view(), name="rollback"),
    path("updates/upload/", views.UploadView.as_view(), name="upload"),
    path("updates/apply/<slug:component>/", views.ApplyView.as_view(), name="apply"),
    path("updates/queue/", views.QueueView.as_view(), name="queue"),
    path("updates/job/<int:pk>/", views.JobView.as_view(), name="job"),
    path("updates/job/<int:pk>/status/", views.JobStatusView.as_view(), name="job_status"),
    path("updates/job/<int:pk>/cancel/", views.JobCancelView.as_view(), name="job_cancel"),
]
