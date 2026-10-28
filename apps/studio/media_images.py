"""Responsive image files for Studio pages: smaller WebP renditions are made once, when a page is published (not while visitors browse)."""
import io
import logging

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

WIDTHS = (480, 960, 1600)
SKIP_TYPES = {"image/gif", "image/svg+xml"}


def _name(media, width: int) -> str:
    stamp = int(media.updated_at.timestamp()) if getattr(media, "updated_at", None) else 0
    return f"studio/r/{media.pk}-{stamp}-{width}.webp"


def renditions(media, *, make: bool) -> list:
    """[(url, width)] of the renditions narrower than the original (existing ones; created when ``make``), smallest first."""
    if not media.width or media.mime_type in SKIP_TYPES or not media.file:
        return []
    out = []
    todo = [w for w in WIDTHS if w < media.width]
    for width in todo:
        name = _name(media, width)
        try:
            if default_storage.exists(name):
                out.append((default_storage.url(name), width))
                continue
            if not make:
                continue
            from PIL import Image

            with media.file.open("rb") as fh:
                img = Image.open(fh)
                img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
            ratio = width / img.width
            img = img.resize((width, max(1, round(img.height * ratio))), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=82, method=4)
            saved = default_storage.save(name, ContentFile(buf.getvalue()))
            out.append((default_storage.url(saved), width))
        except Exception:  # noqa: BLE001 — a broken file never breaks publishing; the original image is used
            logger.warning("Ravix Studio could not make a %spx rendition of media %s", width, media.pk, exc_info=True)
    return out
