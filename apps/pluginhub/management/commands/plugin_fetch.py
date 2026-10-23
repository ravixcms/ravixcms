"""
دریافت یک افزونه از مخزن از خط فرمان (برای اپراتور سرور) با همان نصب‌کنندهٔ پنل: HTTPS، SHA-256، اعتبارسنجی بسته،
امضای دیجیتال با کلیدهای مورد اعتماد، جایگذاری اتمی. **نصب و فعال‌سازی نمی‌کند**؛ آن‌ها در پنل با تأیید صریح مدیر انجام می‌شود.
"""
from apps.core.clilang import cli_gettext
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plugins import installer, sources
from apps.plugins.models import PluginPackage
from apps.plugins.services import PluginError


class Command(BaseCommand):
    help = cli_gettext("Downloads a plugin from the registry, verifies integrity and signature, and places it in the install folder (does not install it).")

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--registry", required=True, help=cli_gettext("Registry base URL, for example https://registry.example.com"))
        parser.add_argument("--plugin-version", dest="plugin_version", help=cli_gettext("Version (default: the latest compatible version)"))
        parser.add_argument("--sha256", help=cli_gettext("Expected hash (from the Directory page) for an independent check"))
        parser.add_argument("--dest", help=cli_gettext("Destination folder (default: RAVIX_PLUGIN_INSTALL_DIR)"))
        parser.add_argument("--allow-insecure", action="store_true", help=cli_gettext("Local development only: allow http"))

    def handle(self, *args, slug, registry, plugin_version, sha256, dest, allow_insecure, **opts):
        if allow_insecure and not settings.DEBUG:
            raise CommandError(cli_gettext("--allow-insecure is allowed only with DEBUG on."))
        try:
            src = sources.RemoteSource(registry, allow_insecure=allow_insecure)
            entry = sources.pick_version(src.info(slug), plugin_version)
            if sha256 and sha256.lower() != entry["sha256"]:
                raise installer.InstallError(cli_gettext("The given hash does not match the registry hash; stopped."))
            data = src.download(slug, entry)
            staged = installer.stage(data, source=PluginPackage.Source.REGISTRY, source_ref=f"{src.ref}#{slug}@{entry['version']}", expected_slug=slug,
                                     expected_version=entry["version"], expected_sha256=sha256 or entry["sha256"], expected_fingerprint=entry.get("fingerprint"),
                                     signature=entry.get("signature") or "", key_id=entry.get("signature_key_id") or "")
            installer.place_new(staged, None, root=Path(dest) if dest else None)
        except PluginError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(cli_gettext("%(slug)s %(a2)s was placed (SHA-256 and signature verified: %(a3)s).") % {"slug": slug, "a2": entry['version'], "a3": staged.verification.label or staged.verification.key_id}))
        self.stdout.write(cli_gettext("Content fingerprint: %(a1)s") % {"a1": staged.fingerprint})
        self.stdout.write(cli_gettext("Next step: in the panel “Plugins”, review the details and files and install it with explicit approval. Nothing was installed or enabled automatically."))
