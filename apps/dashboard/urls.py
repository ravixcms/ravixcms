from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="home"),
    path("onboarding/dismiss/", views.OnboardingDismissView.as_view(), name="onboarding_dismiss"),
    path("samples/delete/", views.DeleteSamplesView.as_view(), name="samples_delete"),
]
