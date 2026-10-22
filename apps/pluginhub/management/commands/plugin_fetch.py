"""
دریافت یک افزونه از مخزن از خط فرمان (برای اپراتور سرور) با همان نصب‌کنندهٔ پنل: HTTPS، SHA-256، اعتبارسنجی بسته،
امضای دیجیتال با کلیدهای مورد اعتماد، جایگذاری اتمی. **نصب و فعال‌سازی نمی‌کند**؛ آن‌ها در پنل با تأیید صریح مدیر انجام می‌شود.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import installer, sources
from apps.plugins.models import PluginPackage
from apps.plugins.services import PluginError


class Command(BaseCommand):
    help = "افزونه را از مخزن دریافت، تمامیت و امضا را بررسی و در پوشهٔ نصب قرار می‌دهد (نصب نمی‌کند)."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--registry", required=True, help="نشانی پایهٔ مخزن، مثلاً https://registry.example.com")
        parser.add_argument("--plugin-version", dest="plugin_version", help="نسخه (پیش‌فرض: آخرین نسخهٔ سازگار)")
        parser.add_argument("--sha256", help="هش مورد انتظار (از صفحهٔ Directory) برای بررسی مستقل")
        parser.add_argument("--dest", help="پوشهٔ مقصد (پیش‌فرض: RAVIN_PLUGIN_INSTALL_DIR)")
        parser.add_argument("--allow-insecure", action="store_true", help="فقط توسعهٔ محلی: اجازهٔ http")

    def handle(self, *args, slug, registry, plugin_version, sha256, dest, allow_insecure, **opts):
        if allow_insecure and not settings.DEBUG:
            raise CommandError("--allow-insecure فقط با DEBUG روشن مجاز است.")
        try:
            src = sources.RemoteSource(registry, allow_insecure=allow_insecure)
            entry = sources.pick_version(src.info(slug), plugin_version)
            if sha256 and sha256.lower() != entry["sha256"]:
                raise installer.InstallError("هش داده‌شده با هش مخزن یکی نیست؛ متوقف شد.")
            data = src.download(slug, entry)
            staged = installer.stage(data, source=PluginPackage.Source.REGISTRY, source_ref=f"{src.ref}#{slug}@{entry['version']}", expected_slug=slug,
                                     expected_version=entry["version"], expected_sha256=sha256 or entry["sha256"], expected_fingerprint=entry.get("fingerprint"),
                                     signature=entry.get("signature") or "", key_id=entry.get("signature_key_id") or "")
            installer.place_new(staged, None, root=Path(dest) if dest else None)
        except PluginError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"{slug} {entry['version']} قرار گرفت (SHA-256 و امضا تأیید شد: {staged.verification.label or staged.verification.key_id})."))
        self.stdout.write(f"اثرانگشت محتوا: {staged.fingerprint}")
        self.stdout.write("گام بعد: در پنل «افزونه‌ها» مشخصات و فایل‌ها را بازبینی و با تأیید صریح نصب کنید. نصب/فعال‌سازی خودکار انجام نشد.")
