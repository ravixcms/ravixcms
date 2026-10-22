from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Media


@receiver(post_delete, sender=Media)
def delete_media_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.storage.delete(instance.file.name)
