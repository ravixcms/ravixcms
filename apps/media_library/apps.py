from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class MediaLibraryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.media_library"
    verbose_name = gettext_lazy("Media library")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        from . import signals  # noqa: F401

        register_nav_item(
            NavItem("media.library", gettext_lazy("Media files"), "media_library:media_list", "image", "content", 50, "media.upload",
                    ("media_library:media_list", "media_library:media_upload", "media_library:media_update", "media_library:media_delete"))
        )
