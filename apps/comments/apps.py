from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class CommentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comments"
    verbose_name = gettext_lazy("Comments")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(NavItem("comments.moderation", gettext_lazy("Comments"), "comments:list", "mail", "content", 25, "comments.moderate",
                                  ("comments:list", "comments:bulk")))
