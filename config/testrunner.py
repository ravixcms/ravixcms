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
        # Same story for the list of active plugins (3 s per process; public URLs consult it to find plugin site routes): a long TTL
        # and an empty cache at the start of every test. Saving a plugin row still invalidates it through its signal.
        from apps.plugins import state as plugin_state

        self._plugin_ttl = plugin_state.TTL
        plugin_state.TTL = 3600.0
        self._setup_and_call = SimpleTestCase._setup_and_call

        def setup_and_call(case, result, debug=False):
            maintenance.reset_cache()
            maintenance._cache["on"] = False
            plugin_state.invalidate()
            return self._setup_and_call(case, result, debug)

        SimpleTestCase._setup_and_call = setup_and_call
        self._modules_default = getattr(settings, "BUILTIN_MODULES_DEFAULT_ON", False)
        settings.BUILTIN_MODULES_DEFAULT_ON = True  # the suite exercises the shop; tests of the off state switch it explicitly
        self._media_tmp = tempfile.mkdtemp(prefix="ravix-test-media-")
        self._media_before = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self._media_tmp

    def teardown_test_environment(self, **kwargs):
        from apps.updater import middleware as maintenance

        maintenance.TTL = self._maintenance_ttl
        from apps.plugins import state as plugin_state

        plugin_state.TTL = self._plugin_ttl
        SimpleTestCase._setup_and_call = self._setup_and_call
        settings.MEDIA_ROOT = self._media_before
        settings.BUILTIN_MODULES_DEFAULT_ON = self._modules_default
        settings.LANGUAGE_CODE, settings.RAVIX_CLI_LANG = self._language_before
        shutil.rmtree(self._media_tmp, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
