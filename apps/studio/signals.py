"""Re-render the published pages that use a media file when the file is replaced or deleted; remove a page's layout cache with the page."""
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from apps.content.models import Page, Post
from apps.media_library.models import Media


@receiver(pre_save, sender=Media)
def _remember_file(sender, instance, **kwargs):
    if instance.pk:
        old = Media.objects.filter(pk=instance.pk).values_list("file", "width", "height", "alt_text").first()
        instance._studio_before = old
    else:
        instance._studio_before = None


@receiver(post_save, sender=Media)
def _media_saved(sender, instance, created, **kwargs):
    before = getattr(instance, "_studio_before", None)
    if created or before is None:
        return
    if before != (instance.file.name if instance.file else "", instance.width, instance.height, instance.alt_text):
        from . import services

        services.rebuild_for_media(instance.pk)


@receiver(post_delete, sender=Media)
def _media_deleted(sender, instance, **kwargs):
    from . import services

    services.rebuild_for_media(instance.pk, deleted=True)


@receiver(pre_delete, sender=Page)
@receiver(pre_delete, sender=Post)
def _delete_layout_with_its_target(sender, instance, **kwargs):
    """MySQL/MariaDB cannot defer constraints, so Django first sets the nullable ``page``/``post`` of the layout to NULL and only then deletes it;
    that breaks the CHECK constraint ``studio_document_one_target`` and the page or post could not be deleted. Removing the layout first avoids it."""
    from .models import StudioDocument

    field = "page" if sender is Page else "post"
    StudioDocument.objects.filter(**{field: instance.pk}).delete()
