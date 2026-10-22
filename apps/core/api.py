from rest_framework import generics, serializers
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import SiteSettings


class SiteSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSettings
        fields = (
            "site_name",
            "tagline",
            "description",
            "logo",
            "favicon",
            "admin_email",
            "contact_phone",
            "address",
            "footer_text",
            "registration_open",
            "updated_at",
        )
        read_only_fields = ("logo", "favicon", "updated_at")  # آپلود تصویر از طریق رابط وب انجام می‌شود


class PublicReadAdminWrite(BasePermission):
    """خواندن برای همه، نوشتن فقط برای دارندگان دسترسی settings.manage."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.has_cap("settings.manage"))


class PublicSiteSerializer(SiteSettingsSerializer):
    """نمای عمومی: فقط اطلاعاتی که برای نمایش به بازدیدکنندگان بی‌خطر است."""

    class Meta(SiteSettingsSerializer.Meta):
        fields = ("site_name", "tagline", "description", "logo", "favicon", "contact_phone", "address", "footer_text", "registration_open")


class SiteSettingsAPIView(generics.RetrieveUpdateAPIView):
    permission_classes = [PublicReadAdminWrite]
    http_method_names = ["get", "patch", "put", "head", "options"]

    def get_object(self):
        return SiteSettings.load()

    def get_serializer_class(self):
        user = self.request.user
        if user.is_authenticated and user.has_cap("settings.manage"):
            return SiteSettingsSerializer
        return PublicSiteSerializer
