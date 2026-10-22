"""Mixin های دسترسی (جدا از permissions.py تا هنگام بارگذاری مدل‌ها import چرخه‌ای ایجاد نشود)."""
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied


class CapabilityRequiredMixin(AccessMixin):
    """دسترسی به View را به یک قابلیت محدود می‌کند.

    کاربر ناشناس به صفحه ورود هدایت می‌شود و کاربر بدون دسترسی خطای ۴۰۳ می‌گیرد.
    """

    required_capability: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.required_capability and not request.user.has_cap(self.required_capability):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
