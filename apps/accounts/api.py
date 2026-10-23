"""API نسخه ۱ حساب‌های کاربری (Django REST Framework)."""
from django.utils.translation import gettext, gettext_lazy
from django.contrib.auth import get_user_model
from rest_framework import generics, serializers
from rest_framework.permissions import BasePermission

User = get_user_model()


class HasCapability(BasePermission):
    """دسترسی را به یک capability محدود می‌کند: ``required_capability`` روی View."""

    message = gettext_lazy("You do not have permission to access this section.")

    def has_permission(self, request, view):
        capability = getattr(view, "required_capability", None)
        return bool(request.user and request.user.is_authenticated and (not capability or request.user.has_cap(capability)))


class UserSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "email", "phone", "bio", "role", "role_label", "date_joined")
        read_only_fields = ("id", "username", "role", "role_label", "date_joined")

    def validate_email(self, value):
        value = value.strip().lower()
        qs = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(gettext("This email belongs to another user."))
        return value


class MeView(generics.RetrieveUpdateAPIView):
    """مشاهده و ویرایش پروفایل کاربر جاری."""

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class UserListAPIView(generics.ListAPIView):
    """فهرست کاربران (فقط مدیران)."""

    serializer_class = UserSerializer
    permission_classes = [HasCapability]
    required_capability = "users.view"
    queryset = User.objects.all()
