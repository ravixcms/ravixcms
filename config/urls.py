from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path
from django.views.i18n import JavaScriptCatalog

from apps.core import views as core_views
from apps.core.health import healthz
from apps.themes import assets as theme_assets

# django-admin فقط برای مدیرکل (is_superuser)؛ نقش «مدیر» پنل Ravix را دارد و نباید وارد admin داخلی جنگو شود.
admin.site.has_permission = lambda request: bool(request.user.is_active and request.user.is_superuser)

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("jsi18n/", JavaScriptCatalog.as_view(packages=None), name="javascript-catalog"),
    path("i18n/setlang/", core_views.set_language, name="set_language"),
    path("theme-assets/<str:slug>/<path:path>", theme_assets.serve, name="theme_assets"),  # static of themes installed from the panel
    path("", include("apps.mcp.urls")),  # Ravix MCP: /mcp/, /oauth/*, /.well-known/* (answer 404 while it is switched off)
    path("install/", include("apps.installer.urls")),  # پیش از سایت عمومی؛ پس از نصب ۴۰۴
    path("panel/", include("apps.dashboard.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("panel/", include("apps.core.urls")),
    path("panel/", include("apps.content.urls")),
    path("panel/", include("apps.comments.urls")),
    path("panel/", include("apps.themes.urls")),
    path("panel/builder/", include("apps.studio.legacy_urls")),  # the first page builder's old addresses: redirected to Ravix Studio
    path("panel/studio/", include("apps.studio.urls")),
    path("studio-assets/", include("apps.studio.public_urls")),
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

# Language-neutral root files (sitemap index, robots.txt) — never prefixed.
urlpatterns += [path("", include("apps.public.root_urls"))]

# Pages that plugins declare in ``site_routes``; each pattern matches only the paths an active plugin asked for.
from apps.plugins.urls import site_route_localized_patterns, site_route_plain_patterns  # noqa: E402

urlpatterns += site_route_plain_patterns

# فروشگاه (اختیاری): فقط وقتی ماژول نصب است. مسیرهای عمومی (/shop/...) باید پیش از سایت عمومی باشند.
if settings.SHOP_ENABLED:
    urlpatterns += [path("panel/", include("apps.shop.panel_urls"))]

# سایت عمومی باید آخرین مورد باشد: مسیر «صفحه ثابت» (/<slug>/) هر آدرس تک‌بخشی را می‌پذیرد
# و فقط به‌خاطر ترتیب، مسیرهای پنل و سیستم را نمی‌پوشاند (نامک‌های رزروشده هم در فرم صفحات رد می‌شوند).
# The public site (and the shop) live under a language prefix: /fa/blog/, /en/blog/, /de/blog/ …
# Old unprefixed URLs are redirected by SiteRegionalMiddleware (apps/core/middleware.py).
_public = list(site_route_localized_patterns)
if settings.SHOP_ENABLED:
    _public.append(path("", include("apps.shop.urls")))
_public.append(path("", include("apps.public.urls")))
urlpatterns += i18n_patterns(*_public, prefix_default_language=True)

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"
