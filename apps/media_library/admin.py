from django.contrib import admin

from .models import Media


@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ("display_title", "kind", "size", "uploaded_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("title", "original_name", "alt_text")
