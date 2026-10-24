"""Production static storage: hashed names + compression, but a file missing from the manifest never breaks a page.

``ManifestStaticFilesStorage`` is strict by default: ``{% static 'x' %}`` for a file that ``collectstatic`` has not seen raises
``ValueError: Missing staticfiles manifest entry`` and the whole page answers 500. Themes installed from the panel are not in the
manifest (they are served by ``apps.themes.assets``, not by the static pipeline), and an operator can always forget a
``collectstatic`` after an upgrade, so a missing entry must degrade to the plain, unhashed URL (a missing image / CSS file) instead of a 500.
"""
import logging

from apps.core.clilang import cli_gettext

from django.contrib.staticfiles.storage import StaticFilesStorage
from whitenoise.storage import CompressedManifestStaticFilesStorage

logger = logging.getLogger(__name__)
_reported: set[str] = set()


class RavixStaticFilesStorage(CompressedManifestStaticFilesStorage):
    manifest_strict = False  # a name that is not in the manifest but exists on disk is hashed on the fly instead of raising

    def url(self, name, force=False):
        try:
            return super().url(name, force)
        except ValueError:  # not in the manifest and not in STATIC_ROOT either (collectstatic was not run for it)
            if name not in _reported and len(_reported) < 200:
                _reported.add(name)
                logger.warning(cli_gettext("Static file %r is not in the manifest or STATIC_ROOT; serving its plain URL (run collectstatic)."), name)
            return StaticFilesStorage.url(self, name)
