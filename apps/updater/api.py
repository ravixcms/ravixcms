from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api import HasCapability

from . import versions


class VersionsAPIView(APIView):
    """نسخهٔ هسته، قراردادها، قالب‌ها، افزونه‌ها و شمای دیتابیس. هیچ مسیر/کلید/رمزی را برنمی‌گرداند."""

    permission_classes = [HasCapability]
    required_capability = "updates.view"

    def get(self, request):
        v = versions.component_versions()
        v["themes"] = [{k: t[k] for k in ("slug", "version", "valid")} for t in v["themes"]]  # بدون مسیر روی دیسک
        return Response(v)
