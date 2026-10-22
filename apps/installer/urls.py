from django.urls import path

from . import views

app_name = "installer"
urlpatterns = [path("", views.install, name="install")]
