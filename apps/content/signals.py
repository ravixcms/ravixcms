from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Page, Post


@receiver(post_delete, sender=Post)
def delete_featured_image(sender, instance, **kwargs):
    if instance.featured_image:
        instance.featured_image.storage.delete(instance.featured_image.name)


# ------------------------------------------------------------ ریدایرکت خودکار هنگام تغییر نامک
def _urls(instance):
    from urllib.parse import unquote

    from django.urls import reverse

    name = "public:post_detail" if isinstance(instance, Post) else "public:page_detail"
    return unquote(reverse(name, args=[instance.slug]))


def _remember_old_state(sender, instance, **kwargs):
    instance._ravix_old = None
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        if old is not None:
            instance._ravix_old = (_urls(old), old.is_live)


def _create_redirect_on_slug_change(sender, instance, created, **kwargs):
    old = getattr(instance, "_ravix_old", None)
    instance._ravix_old = None
    if created or old is None:
        return
    old_url, was_live = old
    new_url = _urls(instance)
    if old_url == new_url or not was_live or not instance.is_live:
        return  # پیش‌نویس هرگز عمومی نبوده؛ و مقصد غیرمنتشر ۴۰۴ می‌شد
    from apps.audit.services import log_event

    from .models import Redirect

    Redirect.objects.filter(old_path=new_url).delete()  # نامک قدیمی دوباره استفاده شده؛ ریدایرکت معارض حذف شود
    Redirect.objects.filter(new_path=old_url).update(new_path=new_url)  # بدون زنجیره
    redirect, was_created = Redirect.objects.update_or_create(
        old_path=old_url,
        defaults={"new_path": new_url, "status_code": 301, "is_auto": True, "note": "تغییر خودکار نامک"},
    )
    log_event(None, "create", redirect, f"ریدایرکت ۳۰۱ خودکار ساخته شد: {old_url} ← {new_url}")


for _model in (Post, Page):
    pre_save.connect(_remember_old_state, sender=_model, dispatch_uid=f"ravix_redirect_pre_{_model.__name__}")
    post_save.connect(_create_redirect_on_slug_change, sender=_model, dispatch_uid=f"ravix_redirect_post_{_model.__name__}")
