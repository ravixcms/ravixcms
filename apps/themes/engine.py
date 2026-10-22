"""
موتور رندر محدودشده‌ی قالب‌ها.

قالب‌های سایت با یک ``django.template.Engine`` جدا و قفل‌شده رندر می‌شوند، نه با موتور اصلی پروژه:

- فقط پوشه ``templates/`` همان قالب بارگذاری می‌شود؛ ``{% include %}`` و ``{% extends %}`` نمی‌توانند به قالب‌های پنل،
  قالب‌های قالب دیگر یا فایل‌های سیستم دسترسی پیدا کنند.
- فقط کتابخانه تگ ``ravix_theme`` قابل ``{% load %}`` است؛ ``static``، ``i18n`` و کتابخانه‌های دیگر بارگذاری نمی‌شوند.
- Context Processor ندارد و به ``request``، ``user``، ``settings`` یا ORM دسترسی نمی‌دهد. قالب فقط داده‌های
  ساده‌ای را می‌بیند که ویوی عمومی آگاهانه در اختیارش گذاشته است (``apps/public/dto.py``).
- کد پایتون در پوشه قالب هرگز import یا اجرا نمی‌شود.
"""
import threading

from django.conf import settings
from django.template import Context, Engine

_lock = threading.Lock()
_engines: dict[str, Engine] = {}

TEMPLATE_LIBRARIES = {
    "ravix_theme": "apps.themes.templatetags.ravix_theme",
    # نام منسوخ؛ تا نسخهٔ 2.0 همان کتابخانه است و هنگام بارگذاری هشدار می‌دهد.
    "ravin_theme": "apps.themes.legacy.ravin_theme",
}


def build_engine(templates_dir) -> Engine:
    filesystem = "django.template.loaders.filesystem.Loader"
    loaders = [filesystem] if settings.DEBUG else [("django.template.loaders.cached.Loader", [filesystem])]
    return Engine(
        dirs=[str(templates_dir)],
        app_dirs=False,
        context_processors=[],
        debug=settings.DEBUG,
        loaders=loaders,
        libraries=dict(TEMPLATE_LIBRARIES),
        autoescape=True,
        string_if_invalid="",
    )


def get_engine(theme) -> Engine:
    key = f"{theme.slug}:{theme.path}"
    engine = _engines.get(key)
    if engine is None:
        with _lock:
            engine = _engines.setdefault(key, build_engine(theme.templates_dir))
    return engine


def clear_engines() -> None:
    with _lock:
        _engines.clear()


def render(theme, template_name: str, context: dict) -> str:
    template = get_engine(theme).get_template(template_name)
    return template.render(Context(context))
