from django.db import models


class SeoMeta(models.Model):
    """تنظیمات سئوی یک صفحه/نوشته. به مدل‌های هسته FK ندارد (فقط نوع و شناسه) تا حذف/جابه‌جایی آن‌ها را دچار مشکل نکند."""

    KINDS = (("page", "صفحه"), ("post", "نوشته"))
    kind = models.CharField(max_length=10, choices=KINDS)
    object_id = models.PositiveIntegerField()
    title = models.CharField("عنوان سئو", max_length=120, blank=True)
    description = models.CharField("توضیحات متا", max_length=320, blank=True)
    noindex = models.BooleanField("عدم ایندکس (noindex)", default=False)
    canonical = models.CharField("Canonical", max_length=300, blank=True)
    og_image = models.CharField("تصویر اشتراک‌گذاری", max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "object_id"], name="plugin_seo_toolkit_unique_target")]
