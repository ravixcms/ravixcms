from django.contrib import admin

from .models import Category, Page, Post, Tag

admin.site.register([Category, Tag])


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "author", "publish_at", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "body")


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "status", "updated_at")
    list_filter = ("status",)
