from django.urls import path

from . import public_views

app_name = "studio_public"

urlpatterns = [path("css/<str:digest>.css", public_views.css, name="css")]
