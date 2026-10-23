from django.utils.translation import gettext, gettext_lazy
from django.conf import settings
from django.db import models

from .config import default_config
from .constants import DEFAULT_THEME


class ThemeConfig(models.Model):
    """تنظیمات ظاهر سایت؛ همیشه فقط یک رکورد (singleton).

    - ``active_theme``: slug قالب فعال سایت (منتشرشده).
    - ``published``: سند پیکربندی منتشرشده که بازدیدکنندگان می‌بینند.
    - ``draft``: تغییرات منتشرنشده (پیش‌نویس). ``None`` یعنی تغییری در انتظار انتشار نیست.

    تغییر ظاهر فقط همین رکورد را عوض می‌کند و هیچ داده‌ای از نوشته‌ها، صفحات یا رسانه‌ها را تغییر نمی‌دهد یا حذف نمی‌کند.
    """

    active_theme = models.CharField(gettext_lazy("Active theme"), max_length=60, default=DEFAULT_THEME)
    published = models.JSONField(gettext_lazy("Published configuration"), default=default_config)
    draft = models.JSONField(gettext_lazy("Draft changes"), null=True, blank=True, default=None)
    draft_updated_at = models.DateTimeField(gettext_lazy("Last draft edit"), null=True, blank=True)
    published_at = models.DateTimeField(gettext_lazy("Last publish time"), null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", verbose_name=gettext_lazy("Published by"),
    )
    updated_at = models.DateTimeField(gettext_lazy("Last edit"), auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Site appearance settings")
        verbose_name_plural = gettext_lazy("Site appearance settings")

    def __str__(self):
        return (gettext("Active theme: %(active_theme)s") % {"active_theme": self.active_theme})

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # حذف تنظیمات ظاهر ممکن نیست
        pass

    @classmethod
    def load(cls) -> "ThemeConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def has_draft(self) -> bool:
        return self.draft is not None
