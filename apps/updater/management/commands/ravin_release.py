"""ساخت و امضای بستهٔ به‌روزرسانی (سمت ناشر). نمونه: ravin_release core --out dist/ --key-file keys/release.key"""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import signing as plugin_signing
from apps.updater import release
from apps.updater.errors import UpdateError


class Command(BaseCommand):
    help = "ساخت بستهٔ هسته/قالب، امضا و تولید index.json برای انتشار (روی ماشین ناشر)."

    def add_arguments(self, p):
        p.add_argument("what", choices=["core", "theme", "keygen"])
        p.add_argument("--out", default="dist")
        p.add_argument("--source", default=str(settings.BASE_DIR), help="ریشهٔ سورس هسته")
        p.add_argument("--theme-dir", default="")
        p.add_argument("--key-file", default="", help="فایل seed کلید خصوصی (مجوز ۶۰۰) — یا متغیر محیطی RAVIN_RELEASE_SIGNING_KEY")
        p.add_argument("--python", default="", help="بازهٔ Python مثل >=3.11")
        p.add_argument("--min-from", default="", help="کمترین نسخهٔ مبدأ")
        p.add_argument("--notes", default="")
        p.add_argument("--no-sign", action="store_true", help="بدون امضا (سایت‌ها نصب را رد می‌کنند)")

    def _seed(self, o):
        if o["no_sign"]:
            return None
        try:
            if o["key_file"]:
                return plugin_signing.read_key_file(o["key_file"])
        except plugin_signing.SigningError as exc:
            raise CommandError(str(exc))
        seed = os.environ.get("RAVIN_RELEASE_SIGNING_KEY")
        if not seed:
            raise CommandError("کلید امضا داده نشد: --key-file یا RAVIN_RELEASE_SIGNING_KEY (یا --no-sign).")
        return seed

    def handle(self, *a, **o):
        out = Path(o["out"])
        if o["what"] == "keygen":
            if not o["key_file"]:
                raise CommandError("--key-file لازم است.")
            seed, pub = plugin_signing.generate_keypair()
            kf = Path(o["key_file"])
            fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(seed)
            self.stdout.write(f"کلید خصوصی در {kf} نوشته شد (مجوز ۶۰۰؛ آن را نگه‌داری و پشتیبان امن کنید). کلید عمومی برای RAVIN_TRUSTED_PUBLIC_KEYS:\n{pub}")
            return
        seed = self._seed(o)
        req = {k: v for k, v in {"python": o["python"], "min_from": o["min_from"]}.items() if v}
        try:
            if o["what"] == "core":
                from apps.core.version import PLUGIN_API_VERSION, THEME_API_VERSION

                req.update(plugin_api=PLUGIN_API_VERSION, theme_api=THEME_API_VERSION)
                entry = release.build_core(Path(o["source"]), out, seed=seed, requires=req, notes=o["notes"])
                path = release.write_index(out, core=[entry], seed=seed)
            else:
                if not o["theme_dir"]:
                    raise CommandError("--theme-dir لازم است.")
                slug, entry = release.build_theme(Path(o["theme_dir"]), out, seed=seed, requires=req, notes=o["notes"])
                path = release.write_index(out, themes={slug: [entry]}, seed=seed)
        except UpdateError as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"بسته ساخته شد: {entry['download_url']} (sha256 {entry['sha256'][:16]}…، {'امضاشده' if seed else 'بدون امضا'})\nفهرست: {path}")
