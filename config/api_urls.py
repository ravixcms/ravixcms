"""مسیرهای API نسخه ۱. هر ماژول جدید مسیرهای API خود را اینجا include می‌کند."""
from django.urls import path

from apps.accounts import api as accounts_api
from apps.core import api as core_api
from apps.dashboard import api as dashboard_api
from django.conf import settings

from apps.updater import api as updater_api

app_name = "api"

urlpatterns = [
    path("me/", accounts_api.MeView.as_view(), name="me"),
    path("users/", accounts_api.UserListAPIView.as_view(), name="users"),
    path("site/", core_api.SiteSettingsAPIView.as_view(), name="site"),
    path("system/versions/", updater_api.VersionsAPIView.as_view(), name="system-versions"),
    path("dashboard/stats/", dashboard_api.DashboardStatsAPIView.as_view(), name="dashboard-stats"),
]

if settings.SHOP_ENABLED:  # API فروشگاه فقط وقتی ماژول نصب است
    from apps.shop.api import urlpatterns as shop_urlpatterns

    urlpatterns += shop_urlpatterns
