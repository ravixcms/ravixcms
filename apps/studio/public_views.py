import re

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

from .models import StudioVersion

_DIGEST = re.compile(r"^[0-9a-f]{20}$")


@require_GET
def css(request, digest):
    """The stylesheet of a large Studio page, addressed by the hash of its text (so it can be cached forever)."""
    if not _DIGEST.match(digest):
        raise Http404
    row = StudioVersion.objects.filter(css_hash=digest).exclude(css="").values_list("css", flat=True).first()
    if row is None:
        raise Http404
    response = HttpResponse(row, content_type="text/css; charset=utf-8")
    response["Cache-Control"] = "public, max-age=31536000, immutable"
    response["X-Content-Type-Options"] = "nosniff"
    return response
