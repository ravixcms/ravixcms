from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api import HasCapability

from . import services


class DashboardStatsAPIView(APIView):
    permission_classes = [HasCapability]
    required_capability = "dashboard.stats"

    def get(self, request):
        return Response(services.site_stats())
