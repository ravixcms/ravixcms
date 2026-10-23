"""امضای یک بستهٔ zip (برای ناشران/توسعه‌دهندگان). خروجی: امضای base64 و شناسهٔ کلید."""
from apps.core.clilang import cli_gettext
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.plugins import signing
from apps.plugins.package import PackageError, inspect


class Command(BaseCommand):
    help = cli_gettext("Signs a zip package with a private key (file).")

    def add_arguments(self, parser):
        parser.add_argument("package")
        parser.add_argument("--key-file", required=True, help=cli_gettext("Private key seed file (permissions 600)"))

    def handle(self, *args, package, key_file, **opts):
        try:
            info = inspect(Path(package).read_bytes())
            seed = signing.read_key_file(key_file)
            sig, kid = signing.sign(seed, info.manifest.slug, info.manifest.version, info.sha256, info.fingerprint)
        except (OSError, PackageError, signing.SigningError, ValueError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"slug: {info.manifest.slug}\nversion: {info.manifest.version}\nsha256: {info.sha256}")
        self.stdout.write(f"key_id: {kid}")
        self.stdout.write(f"signature: {sig}")
