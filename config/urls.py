from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.health import healthz

# django-admin فقط برای مدیرکل (is_superuser)؛ نقش «مدیر» پنل Ravix را دارد و نباید وارد admin داخلی جنگو شود.
admin.site.has_permission = lambda request: bool(request.user.is_active and request.user.is_superuser)

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("panel/", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("panel/", include("apps.core.urls")),
    path("panel/", include("apps.content.urls")),
    path("panel/", include("apps.comments.urls")),
    path("panel/", include("apps.themes.urls")),
    path("panel/builder/", include("apps.builder.urls")),
    path("panel/media/", include("apps.media_library.urls")),
    path("panel/audit/", include("apps.audit.urls")),
    path("panel/", include("apps.sites.urls")),
    path("panel/", include("apps.updater.urls")),
    path("", include("apps.pluginhub.urls")),
    path("", include("apps.plugins.urls")),
    path("api/v1/", include("config.api_urls")),
    path(f"{settings.RAVIX_ADMIN_PATH}/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# فروشگاه (اختیاری): فقط وقتی ماژول نصب است. مسیرهای عمومی (/shop/...) باید پیش از سایت عمومی باشند.
if settings.SHOP_ENABLED:
    urlpatterns += [path("panel/", include("apps.shop.panel_urls")), path("", include("apps.shop.urls"))]

# سایت عمومی باید آخرین مورد باشد: مسیر «صفحه ثابت» (/<slug>/) هر آدرس تک‌بخشی را می‌پذیرد
# و فقط به‌خاطر ترتیب، مسیرهای پنل و سیستم را نمی‌پوشاند (نامک‌های رزروشده هم در فرم صفحات رد می‌شوند).
urlpatterns += [path("", include("apps.public.urls"))]

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
