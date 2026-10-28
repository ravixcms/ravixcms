"""
Lite video embeds: the page shows a cover image and a play link; the player (an iframe on a privacy-friendly host) is created only after a click.

The cover image is fetched once by the server (when the page is published) and stored in the media storage, so a visitor's browser never contacts
YouTube, Vimeo or Aparat before clicking. Only fixed hosts are contacted, with the video ID checked by a strict pattern; redirects are not followed.
"""
import io
import json
import logging
import re
import urllib.error
import urllib.request

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

MAX_BYTES = 2_000_000
TIMEOUT = 5
EMBED_HOSTS = {"youtube": "www.youtube-nocookie.com", "vimeo": "player.vimeo.com", "aparat": "www.aparat.com"}
WATCH_URL = {
    "youtube": "https://www.youtube.com/watch?v={id}",
    "vimeo": "https://vimeo.com/{id}",
    "aparat": "https://www.aparat.com/v/{id}",
}
ID_RE = {"youtube": re.compile(r"^[A-Za-z0-9_-]{11}$"), "vimeo": re.compile(r"^\d{5,12}$"), "aparat": re.compile(r"^[A-Za-z0-9]{3,20}$")}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_get(url: str) -> bytes:
    """GET a fixed https URL, no redirects, capped size. Tests replace this function."""
    if not url.startswith("https://"):
        raise ValueError("https only")
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Ravix-Studio/1.0"})
    with opener.open(req, timeout=TIMEOUT) as resp:  # noqa: S310 — host and path are built from fixed templates and a validated ID
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("too large")
    return data


def _candidates(provider: str, vid: str) -> list:
    if provider == "youtube":
        return [f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"]
    if provider == "vimeo":
        meta = json.loads(_http_get(f"https://vimeo.com/api/oembed.json?url=https%3A//vimeo.com/{vid}"))
        thumb = str(meta.get("thumbnail_url", ""))
        return [thumb] if re.match(r"^https://i\.vimeocdn\.com/[A-Za-z0-9_/.\-?=&%]+$", thumb) else []
    if provider == "aparat":
        meta = json.loads(_http_get(f"https://www.aparat.com/etc/api/video/videohash/{vid}"))
        video = meta.get("video", {}) if isinstance(meta, dict) else {}
        thumb = str(video.get("big_poster") or video.get("small_poster") or "")
        return [thumb] if re.match(r"^https://[A-Za-z0-9.\-]*aparat\.[a-z]+/[A-Za-z0-9_/.\-?=&%]+$", thumb) else []
    return []


def _poster_name(provider: str, vid: str) -> str:
    return f"studio/posters/{provider}-{vid}.webp"


def poster(provider: str, vid: str, *, fetch: bool):
    """(url, width, height) of the stored cover, or None. With ``fetch`` it is downloaded once when missing."""
    if not ID_RE[provider].match(vid):
        return None
    name = _poster_name(provider, vid)
    try:
        if not default_storage.exists(name):
            if not fetch:
                return None
            data = None
            for url in _candidates(provider, vid):
                try:
                    data = _http_get(url)
                    break
                except (urllib.error.URLError, ValueError, OSError):
                    continue
            if not data:
                return None
            from PIL import Image

            img = Image.open(io.BytesIO(data))
            img.load()
            img = img.convert("RGB")
            if img.width > 960:
                img = img.resize((960, max(1, round(img.height * 960 / img.width))), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=80, method=4)
            default_storage.save(name, ContentFile(buf.getvalue()))
            return default_storage.url(name), img.width, img.height
        from PIL import Image

        with default_storage.open(name, "rb") as fh:
            img = Image.open(fh)
            return default_storage.url(name), img.width, img.height
    except Exception:  # noqa: BLE001 — no cover image is fine: the player shows a plain background
        logger.info("Ravix Studio has no cover image for %s:%s", provider, vid, exc_info=True)
        return None
