"""ساخت و امضای بستهٔ به‌روزرسانی (سمت ناشر). نمونه: ravix_release core --out dist/ --key-file keys/release.key"""
from apps.core.clilang import cli_gettext
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.deprecation import getenv
from apps.plugins import signing as plugin_signing
from apps.updater import release
from apps.updater.errors import UpdateError


class Command(BaseCommand):
    help = cli_gettext("Builds a core/theme package, signs it and generates index.json for release (on the publisher's machine).")

    def add_arguments(self, p):
        p.add_argument("what", choices=["core", "theme", "keygen"])
        p.add_argument("--out", default="dist")
        p.add_argument("--source", default=str(settings.BASE_DIR), help=cli_gettext("Root of the core source"))
        p.add_argument("--theme-dir", default="")
        p.add_argument("--key-file", default="", help=cli_gettext("Private key seed file (permissions 600) — or the environment variable RAVIX_RELEASE_SIGNING_KEY"))
        p.add_argument("--python", default="", help=cli_gettext("Python range such as >=3.11"))
        p.add_argument("--min-from", default="", help=cli_gettext("Minimum source version"))
        p.add_argument("--notes", default="")
        p.add_argument("--notes-i18n", default="", help=cli_gettext("JSON file with the release notes per language, such as {\"fa\": \"…\", \"en\": \"…\"}"))
        p.add_argument("--wheelhouse", default="", help=cli_gettext("Folder of .whl files to carry in the package (only for releases that change Python packages; shared hosting installs them offline)"))
        p.add_argument("--channel", default="stable", help=cli_gettext("Release channel written into index.json (stable, beta, …)"))
        p.add_argument("--no-sign", action="store_true", help=cli_gettext("Unsigned (sites will refuse to install it)"))

    def _seed(self, o):
        if o["no_sign"]:
            return None
        try:
            if o["key_file"]:
                return plugin_signing.read_key_file(o["key_file"])
        except plugin_signing.SigningError as exc:
            raise CommandError(str(exc))
        seed = getenv("RAVIX_RELEASE_SIGNING_KEY")
        if not seed:
            raise CommandError(cli_gettext("No signing key was given: --key-file or RAVIX_RELEASE_SIGNING_KEY (or --no-sign)."))
        return seed

    def handle(self, *a, **o):
        out = Path(o["out"])
        if o["what"] == "keygen":
            if not o["key_file"]:
                raise CommandError(cli_gettext("--key-file is required."))
            seed, pub = plugin_signing.generate_keypair()
            kf = Path(o["key_file"])
            fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(seed)
            self.stdout.write(cli_gettext("Private key written to %(kf)s (permissions 600; keep it and make a safe backup). Key id (key_id; for revocation): %(key_id)s\nPublic key (for apps/core/official.py or RAVIX_TRUSTED_PUBLIC_KEYS; the last line):\n%(pub)s") % {"kf": kf, "key_id": plugin_signing.key_id(pub), "pub": pub})
            return
        seed = self._seed(o)
        import json, re

        if not re.match(r"^[a-z][a-z0-9-]{0,11}$", o["channel"]):
            raise CommandError(cli_gettext("--channel must be a short lowercase name such as stable or beta."))
        notes_i18n = None
        if o["notes_i18n"]:
            try:
                notes_i18n = json.loads(Path(o["notes_i18n"]).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CommandError(cli_gettext("The notes file could not be read: %(exc)s") % {"exc": exc})
        req = {k: v for k, v in {"python": o["python"], "min_from": o["min_from"]}.items() if v}
        try:
            if o["what"] == "core":
                from apps.core.version import PLUGIN_API_VERSION, THEME_API_VERSION

                req.update(plugin_api=PLUGIN_API_VERSION, theme_api=THEME_API_VERSION)
                entry = release.build_core(Path(o["source"]), out, seed=seed, requires=req, notes=o["notes"], notes_i18n=notes_i18n, wheelhouse=Path(o["wheelhouse"]) if o["wheelhouse"] else None)
                path = release.write_index(out, core=[entry], seed=seed, channel=o["channel"])
            else:
                if not o["theme_dir"]:
                    raise CommandError(cli_gettext("--theme-dir is required."))
                slug, entry = release.build_theme(Path(o["theme_dir"]), out, seed=seed, requires=req, notes=o["notes"], notes_i18n=notes_i18n)
                path = release.write_index(out, themes={slug: [entry]}, seed=seed, channel=o["channel"])
        except UpdateError as exc:
            raise CommandError(str(exc))
        signed_label = cli_gettext("signed") if seed else cli_gettext("unsigned")
        self.stdout.write(cli_gettext("Package built: %(a1)s (sha256 %(a2)s…, %(signed_label)s)\nIndex: %(path)s") % {"a1": entry['download_url'], "a2": entry['sha256'][:16], "signed_label": signed_label, "path": path})
