from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.content"
    verbose_name = gettext_lazy("Content")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        from . import signals  # noqa: F401

        items = [
            NavItem("content.posts", gettext_lazy("Posts"), "content:post_list", "file", "content", 10, "content.write",
                    ("content:post_list", "content:post_create", "content:post_update", "content:post_delete", "content:post_detail")),
            NavItem("content.pages", gettext_lazy("Pages"), "content:page_list", "pages", "content", 20, "pages.manage",
                    ("content:page_list", "content:page_create", "content:page_update", "content:page_delete", "content:page_detail")),
            NavItem("content.categories", gettext_lazy("Content categories"), "content:category_list", "folder", "content", 30, "taxonomy.manage",
                    ("content:category_list", "content:category_create", "content:category_update", "content:category_delete")),
            NavItem("content.tags", gettext_lazy("Tags"), "content:tag_list", "tag", "content", 40, "taxonomy.manage",
                    ("content:tag_list", "content:tag_create", "content:tag_update", "content:tag_delete")),
            NavItem("content.redirects", gettext_lazy("Redirects"), "content:redirect_list", "globe", "content", 50, "redirects.manage",
                    ("content:redirect_list", "content:redirect_create", "content:redirect_update", "content:redirect_delete")),
        ]
        for item in items:
            register_nav_item(item)
