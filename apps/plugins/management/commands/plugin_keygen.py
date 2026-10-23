"""ساخت جفت‌کلید Ed25519 برای امضای بسته. کلید خصوصی در فایل با مجوز 600 نوشته می‌شود و **هرگز چاپ نمی‌شود**."""
from apps.core.clilang import cli_gettext
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import signing


class Command(BaseCommand):
    help = cli_gettext("Creates a signing key pair: the private key goes to --out (600), the public key is printed.")

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help=cli_gettext("Path of the private key file (outside the code repository!)"))

    def handle(self, *args, out, **opts):
        path = Path(out).resolve()
        if path.exists():
            raise CommandError(cli_gettext("The file already exists; not overwriting."))
        if str(path).startswith(str(Path(settings.BASE_DIR).resolve()) + os.sep):
            self.stderr.write(self.style.WARNING(cli_gettext("Warning: the private key is inside the project folder; move it outside the code repository and never commit it.")))
        seed, pub = signing.generate_keypair()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(seed + "\n")
        self.stdout.write(cli_gettext("Private key written: %(path)s (permissions 600; keep a safe backup, it cannot be recovered)") % {"path": path})
        self.stdout.write(cli_gettext("Public key (to register on sites): %(pub)s") % {"pub": pub})
        self.stdout.write(cli_gettext("Key id: %(a1)s") % {"a1": signing.key_id(pub)})
