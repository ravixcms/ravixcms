from django.urls import path

from . import views

app_name = "updater"

urlpatterns = [
    path("updates/", views.HomeView.as_view(), name="home"),
    path("updates/check/", views.CheckView.as_view(), name="check"),
    path("updates/run/<int:pk>/", views.RunDetailView.as_view(), name="run"),
    path("updates/run/<int:pk>/rollback/", views.RollbackView.as_view(), name="rollback"),
    path("updates/apply/<slug:component>/", views.ApplyView.as_view(), name="apply"),
]
