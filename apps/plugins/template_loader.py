"""لودر قالب افزونه‌ها: فقط نام‌های ``plugin:<slug>/<مسیر>`` را از پوشهٔ ``templates/`` همان افزونه می‌خواند.

افزونه‌ها نمی‌توانند قالب‌های هسته را بپوشانند (نام‌ها فضای نام جدا دارند) و فقط افزونهٔ فعال و تأییدشده قالب می‌دهد.
"""
from django.template import Origin, TemplateDoesNotExist
from django.template.loaders.base import Loader as BaseLoader
from django.utils._os import safe_join

PREFIX = "plugin:"


class Loader(BaseLoader):
    def get_template_sources(self, template_name):
        if not template_name.startswith(PREFIX) or "/" not in template_name:
            return
        slug, _, rel = template_name[len(PREFIX):].partition("/")
        from . import registry

        loaded = registry.get(slug)
        if loaded is None:
            return
        try:
            path = safe_join(loaded.root / "templates", rel)
        except Exception:  # noqa: BLE001 - تلاش برای خروج از پوشه
            return
        yield Origin(name=path, template_name=template_name, loader=self)

    def get_contents(self, origin):
        try:
            with open(origin.name, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            raise TemplateDoesNotExist(origin)
