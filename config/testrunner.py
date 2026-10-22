"""اجراگر آزمون Ravix: کل اجرا با MEDIA_ROOT موقت انجام می‌شود تا آزمون‌ها هرگز در ``media/`` واقعی پروژه فایل نگذارند."""
import shutil
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner


class RavixTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media_tmp = tempfile.mkdtemp(prefix="ravix-test-media-")
        self._media_before = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._media_tmp

    def teardown_test_environment(self, **kwargs):
        settings.MEDIA_ROOT = self._media_before
        shutil.rmtree(self._media_tmp, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
