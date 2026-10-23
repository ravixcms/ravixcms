"""اجراگر آزمون Ravix: کل اجرا با MEDIA_ROOT موقت انجام می‌شود تا آزمون‌ها هرگز در ``media/`` واقعی پروژه فایل نگذارند."""
import shutil
import tempfile

from django.conf import settings
from django.test.runner import DiscoverRunner


class RavixTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # The suite was written against Persian as the default language (gettext outside a request, command output, log lines),
        # whatever LANGUAGE_CODE the environment has; tests of other languages switch explicitly.
        self._language_before = (settings.LANGUAGE_CODE, getattr(settings, "RAVIX_CLI_LANG", ""))
        settings.LANGUAGE_CODE, settings.RAVIX_CLI_LANG = "fa", ""
        from django.utils.translation import trans_real

        trans_real._default = None
        self._media_tmp = tempfile.mkdtemp(prefix="ravix-test-media-")
        self._media_before = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._media_tmp

    def teardown_test_environment(self, **kwargs):
        settings.MEDIA_ROOT = self._media_before
        settings.LANGUAGE_CODE, settings.RAVIX_CLI_LANG = self._language_before
        shutil.rmtree(self._media_tmp, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
