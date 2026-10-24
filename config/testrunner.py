"""اجراگر آزمون Ravix: کل اجرا با MEDIA_ROOT موقت انجام می‌شود تا آزمون‌ها هرگز در ``media/`` واقعی پروژه فایل نگذارند."""
import shutil
import tempfile

from django.conf import settings
from django.test import SimpleTestCase
from django.test.runner import DiscoverRunner


class RavixTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # The suite was written against Persian as the default language (gettext outside a request, command output, log lines),
        # whatever LANGUAGE_CODE the environment has; tests of other languages switch explicitly.
        self._language_before = (settings.LANGUAGE_CODE, getattr(settings, "RAVIX_CLI_LANG", ""))
        settings.LANGUAGE_CODE, settings.RAVIX_CLI_LANG = "fa", "fa"
        from django.utils.translation import trans_real

        trans_real._default = None
        # The maintenance middleware remembers "is an update running?" for 1 second per process. A test that counts queries around two
        # requests failed about one run in five (parallel runs) when that second ended between them ("8 != 7": one extra
        # SELECT on updater_updatelock). Tests now get a long TTL and a fresh answer at the start of every test.
        from apps.updater import middleware as maintenance

        self._maintenance_ttl = maintenance.TTL
        maintenance.TTL = 3600.0
        self._setup_and_call = SimpleTestCase._setup_and_call

        def setup_and_call(case, result, debug=False):
            maintenance.reset_cache()
            maintenance._cache["on"] = False
            return self._setup_and_call(case, result, debug)

        SimpleTestCase._setup_and_call = setup_and_call
        self._media_tmp = tempfile.mkdtemp(prefix="ravix-test-media-")
        self._media_before = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._media_tmp

    def teardown_test_environment(self, **kwargs):
        from apps.updater import middleware as maintenance

        maintenance.TTL = self._maintenance_ttl
        SimpleTestCase._setup_and_call = self._setup_and_call
        settings.MEDIA_ROOT = self._media_before
        settings.LANGUAGE_CODE, settings.RAVIX_CLI_LANG = self._language_before
        shutil.rmtree(self._media_tmp, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
