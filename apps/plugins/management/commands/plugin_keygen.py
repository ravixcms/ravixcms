"""ساخت جفت‌کلید Ed25519 برای امضای بسته. کلید خصوصی در فایل با مجوز 600 نوشته می‌شود و **هرگز چاپ نمی‌شود**."""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import signing


class Command(BaseCommand):
    help = "جفت‌کلید امضا می‌سازد: کلید خصوصی در --out (600)، کلید عمومی چاپ می‌شود."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="مسیر فایل کلید خصوصی (خارج از مخزن کد!)")

    def handle(self, *args, out, **opts):
        path = Path(out).resolve()
        if path.exists():
            raise CommandError("فایل از قبل وجود دارد؛ بازنویسی نمی‌کنیم.")
        if str(path).startswith(str(Path(settings.BASE_DIR).resolve()) + os.sep):
            self.stderr.write(self.style.WARNING("هشدار: کلید خصوصی داخل پوشهٔ پروژه است؛ آن را به مسیری بیرون از مخزن کد ببرید و هرگز commit نکنید."))
        seed, pub = signing.generate_keypair()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(seed + "\n")
        self.stdout.write(f"کلید خصوصی نوشته شد: {path} (مجوز 600؛ پشتیبان امن بگیرید، قابل بازیابی نیست)")
        self.stdout.write(f"کلید عمومی (برای ثبت در سایت‌ها): {pub}")
        self.stdout.write(f"شناسهٔ کلید: {signing.key_id(pub)}")
