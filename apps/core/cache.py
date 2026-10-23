"""File cache that never stops the site when its folder is missing or not writable.

Django's ``FileBasedCache`` creates its folder while it is being constructed, which happens on the first cache access (for
example during ``manage.py check``) and raises ``PermissionError`` when the process cannot write there. This backend:

* does not touch the disk when it is created, and creates the folder only when something is written;
* turns any ``OSError`` from the folder into "cache miss / nothing stored" so the page being served does not turn into a 500;
* remembers that the last write failed (``broken``): login throttling then uses the database cache table instead of silently
  doing nothing, and ``ravix_preflight`` and the site-health page report the problem clearly. The folder itself is provisioned by
  systemd (``CacheDirectory=ravix``) or by the operator, never by the settings module.
"""
import logging
import time

from apps.core.clilang import cli_gettext
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.filebased import FileBasedCache

logger = logging.getLogger(__name__)
_WARN_EVERY = 60  # seconds: a broken folder must not fill the log on every request


class SafeFileBasedCache(FileBasedCache):
    def __init__(self, dir, params):  # noqa: A002 - Django's signature
        self.broken = False
        self._last_warn = 0.0
        super().__init__(dir, params)

    def _createdir(self):
        try:
            super()._createdir()
        except OSError as exc:
            self._failed(exc)

    def _failed(self, exc):
        self.broken = True
        now = time.monotonic()
        if now - self._last_warn >= _WARN_EVERY:
            self._last_warn = now
            logger.error(cli_gettext("The cache folder %(a1)s is missing or not writable (%(a2)s: %(a3)s). Provision it with CacheDirectory=ravix in the systemd unit "
                                     "or set RAVIX_CACHE_DIR; until then nothing is cached and login throttling uses the database table.")
                         % {"a1": self._dir, "a2": type(exc).__name__, "a3": exc})

    # Reads never raise; they just miss.
    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except OSError as exc:
            self._failed(exc)
            return default

    def has_key(self, key, version=None):
        try:
            return super().has_key(key, version)
        except OSError as exc:
            self._failed(exc)
            return False

    # Writes: success clears the flag, failure sets it.
    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            super().set(key, value, timeout, version)
        except OSError as exc:
            self._failed(exc)
        else:
            self.broken = False

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            stored = super().add(key, value, timeout, version)  # its inner set() swallows folder errors and records them
        except OSError as exc:
            self._failed(exc)
            return False
        return False if self.broken else stored

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().touch(key, timeout, version)
        except OSError as exc:
            self._failed(exc)
            return False

    def delete(self, key, version=None):
        try:
            return super().delete(key, version)
        except OSError as exc:
            self._failed(exc)
            return False

    def clear(self):
        try:
            super().clear()
        except OSError as exc:
            self._failed(exc)
