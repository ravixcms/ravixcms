"""The maintenance middleware's per-process 1 s cache must not make query-counting tests flaky (shop "8 != 7" failure)."""
import time
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.plugins import state as plugin_state
from apps.updater import middleware


class MaintenanceCacheStabilityTests(TestCase):
    def test_test_runs_use_a_long_ttl_and_a_fresh_answer_per_test(self):
        self.assertGreaterEqual(middleware.TTL, 3600)

    def test_a_second_passing_between_two_requests_adds_no_query(self):
        self.client.get("/healthz")
        self.client.get("/fa/")
        with CaptureQueriesContext(connection) as first:
            self.client.get("/fa/")
        real = time.monotonic
        # time moves for the maintenance cache only: the plugin list has its own 3 s cache (checked when public URLs resolve)
        with mock.patch("apps.updater.middleware.time.monotonic", side_effect=lambda: real() + 5), mock.patch.object(plugin_state, "TTL", 10**6):
            with CaptureQueriesContext(connection) as later:
                self.client.get("/fa/")
        self.assertEqual(len(first), len(later))

    def test_reset_cache_still_forces_a_fresh_check(self):
        middleware.maintenance_active()
        middleware.reset_cache()
        with CaptureQueriesContext(connection) as ctx:
            middleware.maintenance_active()
        self.assertEqual(len(ctx), 1)
