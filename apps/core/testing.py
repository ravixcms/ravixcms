"""ابزارهای مشترک تست."""
import io
import shutil
import tempfile

from django.test import TestCase, override_settings
from PIL import Image


class MediaTestCase(TestCase):
    """هر کلاس تست در یک پوشه موقت MEDIA_ROOT کار می‌کند تا فایل‌های واقعی پروژه دست نخورند."""

    @classmethod
    def setUpClass(cls):
        cls._media_dir = tempfile.mkdtemp(prefix="ravin-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)


def png_bytes(size=(20, 20), color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(size=(20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, "JPEG")
    return buf.getvalue()
