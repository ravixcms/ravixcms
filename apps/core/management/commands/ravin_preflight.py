import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


_ENABLED_OK = {"enabled", "enabled-runtime", "static", "alias", "indirect", "generated"}


def systemd_unit_state(unit: str):
    """(is-enabled, is-active) یک unit systemd؛ None اگر systemd در دسترس نیست (کانتینر/توسعه) یا پاسخ نامعلوم است."""
    out = []
    for verb in ("is-enabled", "is-active"):
        try:
            r = subprocess.run(["systemctl", verb, unit], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        out.append(r.stdout.strip())
    return None if not all(out) else tuple(out)


class Command(BaseCommand):
    help = "بررسی آمادگی استقرار (پیکربندی، امنیت، مهاجرت‌ها، مسیرها). با خطا کد خروج ۱ می‌دهد."

    def handle(self, *args, **options):
        errors, warns, oks = [], [], []

        def need(cond, ok, bad):
            (oks if cond else errors).append(ok if cond else bad)

        def advise(cond, ok, bad):
            (oks if cond else warns).append(ok if cond else bad)

        need(not settings.DEBUG, "DEBUG خاموش است", "DEBUG روشن است")
        need(len(settings.SECRET_KEY) >= 32 and "insecure" not in settings.SECRET_KEY.lower(), "SECRET_KEY قوی است", "SECRET_KEY ضعیف/پیش‌فرض است")
        need("*" not in settings.ALLOWED_HOSTS and settings.ALLOWED_HOSTS, "ALLOWED_HOSTS مشخص است", "ALLOWED_HOSTS خالی یا * است")
        need(connection.vendor == "postgresql", "دیتابیس PostgreSQL است", f"دیتابیس {connection.vendor} است (برای تولید PostgreSQL لازم است)")
        need(getattr(settings, "SECURE_SSL_REDIRECT", False), "ریدایرکت HTTPS فعال است", "SECURE_SSL_REDIRECT خاموش است")
        need(settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE, "کوکی‌ها Secure هستند", "کوکی‌ها Secure نیستند")
        need(getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0, "HSTS فعال است", "HSTS خاموش است")
        advise(bool(settings.CSRF_TRUSTED_ORIGINS), "CSRF_TRUSTED_ORIGINS تنظیم شده", "CSRF_TRUSTED_ORIGINS خالی است (پشت HTTPS با دامنه، معمولاً لازم است)")
        advise(bool(settings.SITE_URL), "SITE_URL تنظیم شده (آدرس‌های مطلق sitemap/canonical)", "SITE_URL خالی است؛ آدرس مطلق از Host درخواست ساخته می‌شود")
        advise("console" not in settings.EMAIL_BACKEND, "ایمیل واقعی پیکربندی شده", "EMAIL_BACKEND کنسولی است؛ ایمیلی ارسال نمی‌شود")
        media = str(settings.MEDIA_ROOT)
        need(os.path.isdir(media) and os.access(media, os.W_OK), "پوشهٔ media قابل نوشتن است", f"پوشهٔ media ({media}) وجود ندارد یا قابل نوشتن نیست (بسازید: mkdir -p {media} و مالکیت را به کاربر سرویس بدهید)")
        advise(os.path.isdir(settings.STATIC_ROOT) and os.listdir(settings.STATIC_ROOT), "collectstatic اجرا شده", "collectstatic اجرا نشده است")
        # --- کش (محدودسازی ورود از کش می‌خواند؛ کش خراب = خطای ۵۰۰ در ورود) ---
        from django.core.cache import caches

        cache_conf = settings.CACHES.get("default", {})
        if cache_conf.get("BACKEND", "").endswith("FileBasedCache"):
            cdir = str(cache_conf.get("LOCATION", ""))
            need(os.path.isdir(cdir) and os.access(cdir, os.W_OK), f"پوشهٔ کش ({cdir}) قابل نوشتن است",
                 f"پوشهٔ کش ({cdir}) وجود ندارد یا برای کاربر سرویس قابل نوشتن نیست (در ravin.service از CacheDirectory=ravin استفاده کنید)")
        try:
            probe = "ravin-preflight-probe"
            caches["default"].set(probe, "1", 10)
            ok_cache = caches["default"].get(probe) == "1"
            caches["default"].delete(probe)
        except Exception as exc:  # noqa: BLE001 - هر خطای کش
            ok_cache, cache_err = False, f"{type(exc).__name__}: {exc}"
        else:
            cache_err = "مقدار نوشته‌شده خوانده نشد"
        need(ok_cache, "کش اصلی کار می‌کند (نوشتن/خواندن آزمایشی موفق)", f"کش اصلی کار نمی‌کند ({cache_err}). ورود به پنل بدون کش سالم به کش جدولی دیتابیس برمی‌گردد و هشدار لاگ می‌دهد")
        if "throttle_fallback" in settings.CACHES:
            try:
                from apps.core.models import ThrottleCacheEntry

                ThrottleCacheEntry.objects.count()
                ok_fb = True
            except Exception:  # noqa: BLE001 - پیش از migrate
                ok_fb = False
            advise(ok_fb, "جدول کش جایگزین (ravin_cache) در دیتابیس هست", "جدول کش جایگزین (ravin_cache) نیست؛ migrate را اجرا کنید")
        static_root = str(settings.STATIC_ROOT)
        need(os.path.isdir(static_root) and os.access(static_root, os.R_OK), "STATIC_ROOT وجود دارد و خوانا است", f"STATIC_ROOT ({static_root}) وجود ندارد یا خوانا نیست؛ collectstatic را اجرا کنید")
        # --- تایمرهای systemd: بدون آن‌ها نوشتهٔ زمان‌بندی‌شده منتشر نمی‌شود و پشتیبان گرفته نمی‌شود ---
        timers = [("ravin-publish-scheduled.timer", "انتشار نوشته‌های زمان‌بندی‌شده"), ("ravin-backup.timer", "پشتیبان‌گیری روزانه")]
        if settings.SHOP_ENABLED:
            timers.append(("ravin-expire-orders.timer", "لغو سفارش‌های پرداخت‌نشده"))
        unavailable = False
        for unit, label in timers:
            state = systemd_unit_state(unit)
            if state is None:
                unavailable = True
                continue
            enabled, active = state
            need(enabled in _ENABLED_OK and active == "active", f"تایمر {unit} ({label}) فعال است",
                 f"تایمر {unit} ({label}) فعال نیست (enabled={enabled}، active={active}): sudo cp deploy/{unit[:-6]}.{{service,timer}} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now {unit}")
        if unavailable:
            warns.append("وضعیت تایمرهای systemd (انتشار زمان‌بندی‌شده، پشتیبان، سفارش‌ها) قابل بررسی نبود؛ روی سرور با systemd دوباره اجرا کنید یا با «systemctl list-timers» دستی ببینید")
        from apps.plugins import signing

        install_dir = Path(settings.RAVIN_PLUGIN_INSTALL_DIR)
        for d in settings.RAVIN_PLUGIN_DIRS:
            if settings.RAVIN_PLUGIN_WEB_INSTALL and Path(d) == install_dir:
                continue  # پوشهٔ نصب خودکار عمداً قابل‌نوشتن است
            advise(not os.access(str(d), os.W_OK) or os.geteuid() == 0, f"پوشهٔ افزونه {d} برای این کاربر فقط‌خواندنی است", f"پوشهٔ افزونه {d} برای کاربر سرویس قابل نوشتن است (توصیه: فقط‌خواندنی)")
        # --- به‌روزرسانی یکپارچه ---
        from apps.updater import versions as upd_versions
        from apps.updater.models import UpdateRun

        try:
            stuck = UpdateRun.objects.filter(status="needs_recovery").count()
            need(not stuck, "هیچ ارتقای نیازمند بازیابی نیست", f"{stuck} ارتقا نیازمند بازیابی دستی است (پنل ← به‌روزرسانی‌ها)")
        except Exception:  # noqa: BLE001 - پیش از migrate
            pass
        advise(not upd_versions.restart_pending(), "کد روی دیسک با پروسهٔ در حال اجرا یکی است", "کد روی دیسک جدیدتر از پروسهٔ در حال اجراست؛ سرویس را راه‌اندازی مجدد کنید")
        advise(bool(settings.RAVIN_UPDATE_URL or settings.RAVIN_UPDATE_DIR), "منبع به‌روزرسانی تنظیم شده", "منبع به‌روزرسانی (RAVIN_UPDATE_URL) تنظیم نشده؛ بررسی نسخهٔ جدید غیرفعال است")
        if settings.RAVIN_UPDATE_URL:
            need(settings.RAVIN_UPDATE_URL.startswith("https://"), "منبع به‌روزرسانی HTTPS است", "RAVIN_UPDATE_URL باید https باشد")
        if settings.RAVIN_UPDATE_URL or settings.RAVIN_UPDATE_DIR:
            advise(bool(signing.trusted_keys()), "کلید عمومی مورد اعتماد برای امضای بسته‌ها تنظیم شده", "هیچ کلید مورد اعتمادی (RAVIN_TRUSTED_PUBLIC_KEYS) نیست؛ هیچ بستهٔ به‌روزرسانی پذیرفته نمی‌شود")
        advise(not settings.RAVIN_UPDATE_WEB_APPLY, "اعمال ارتقا از وب خاموش است (توصیه‌شده)", "RAVIN_UPDATE_WEB_APPLY روشن است؛ سرویس وب باید به کد و دیتابیس دسترسی نوشتن داشته باشد")
        try:
            plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
            need(not plan, "همهٔ مهاجرت‌ها اجرا شده", f"{len(plan)} مهاجرت اجرا نشده؛ migrate را اجرا کنید")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"اتصال به دیتابیس ناموفق: {exc}")
        if settings.SHOP_ENABLED:
            from apps.shop.models import GatewayConfig
            from apps.shop.payments import registry as gw_registry, vault as gw_vault

            try:
                rows = list(GatewayConfig.objects.filter(enabled=True))
                classes = gw_registry.available_gateways()
                real = [r for r in rows if r.gateway in classes and not classes[r.gateway].is_test]
                test_on = [r for r in rows if r.gateway in classes and classes[r.gateway].is_test]
                has_secrets = any(r.secrets_encrypted for r in GatewayConfig.objects.all())
            except Exception:  # noqa: BLE001 - پیش از migrate
                real, test_on, has_secrets = [], [], False
            if settings.SHOP_PAYMENT_GATEWAY == "sandbox" and settings.SHOP_ALLOW_SANDBOX_IN_PRODUCTION:
                errors.append("درگاه آزمایشی در تولید مجاز شده است؛ مشتریان واقعی نباید با آن پرداخت کنند")
            elif test_on and not settings.DEBUG:
                errors.append("درگاه آزمایشی در پنل فعال است؛ در تولید غیرفعالش کنید")
            elif real:
                oks.append("درگاه(های) پرداخت فعال: " + "، ".join(sorted({r.gateway for r in real})))
                tm = [r.gateway for r in real if r.test_mode]
                if tm:
                    warns.append("درگاه در حالت آزمایشی فعال است: " + "، ".join(tm))
            else:
                warns.append("فروشگاه روشن ولی درگاه واقعی فعال نیست؛ ثبت سفارش غیرفعال است (RAVIN_SHOP_ENABLED=false برای خاموش‌کردن کامل)")
            if has_secrets or real:
                need(gw_vault.key_configured(), "کلید رمزنگاری اسرار درگاه (RAVIN_SECRETS_KEY) تنظیم شده", "RAVIN_SECRETS_KEY تنظیم نشده؛ کلیدهای درگاه قابل نگهداری/خواندن امن نیستند")
            else:
                advise(gw_vault.key_configured(), "کلید رمزنگاری اسرار درگاه تنظیم شده", "RAVIN_SECRETS_KEY تنظیم نشده (پیش از افزودن کلید درگاه لازم است)")
        if settings.SHOP_ENABLED:
            priv = Path(settings.SHOP_PRIVATE_ROOT).resolve()
            media_root = Path(settings.MEDIA_ROOT).resolve()
            inside_media = priv == media_root or media_root in priv.parents
            need(not inside_media, "پوشهٔ فایل‌های دیجیتال خارج از media عمومی است", f"RAVIN_SHOP_PRIVATE_ROOT ({priv}) داخل media عمومی است؛ فایل‌های دیجیتال لو می‌روند")
            advise(priv.is_dir() and os.access(str(priv), os.W_OK), "پوشهٔ فایل‌های دیجیتال قابل نوشتن است",
                   f"پوشهٔ فایل‌های دیجیتال ({priv}) هنوز ساخته نشده یا قابل نوشتن نیست (با اولین بارگذاری ساخته می‌شود؛ در پشتیبان‌گیری لحاظ کنید)")
        bad_keys = []
        for item in settings.RAVIN_TRUSTED_PUBLIC_KEYS:
            try:
                signing.normalize_public_key(item.partition(":")[2] or item)
            except signing.SigningError:
                bad_keys.append(item[:20])
        need(not bad_keys, "کلیدهای مورد اعتماد (env) معتبرند", f"RAVIN_TRUSTED_PUBLIC_KEYS شامل ورودی نامعتبر است: {bad_keys}")
        if settings.RAVIN_PLUGIN_WEB_INSTALL:
            warns.append("نصب افزونه از طریق وب روشن است (RAVIN_PLUGIN_WEB_INSTALL)؛ فقط اگر لازم دارید روشن بماند، چون افزونه کد قابل اجراست")
            need(install_dir.is_dir() and os.access(str(install_dir), os.W_OK), "پوشهٔ نصب افزونه قابل نوشتن است",
                 f"پوشهٔ نصب افزونه ({install_dir}) وجود ندارد یا قابل نوشتن نیست")
            backup_dir = Path(settings.RAVIN_PLUGIN_BACKUP_DIR)
            need(backup_dir.is_dir() and os.access(str(backup_dir), os.W_OK), "پوشهٔ پشتیبان افزونه‌ها قابل نوشتن است",
                 f"پوشهٔ پشتیبان افزونه‌ها ({backup_dir}) وجود ندارد یا قابل نوشتن نیست")
            advise(bool(signing.trusted_keys()), "کلید مورد اعتماد برای امضا تعریف شده", "هیچ کلید مورد اعتمادی تعریف نشده؛ بستهٔ امضاشده پذیرفته نمی‌شود")
        advise(not settings.RAVIN_PLUGINS_ALLOW_UNSIGNED, "بستهٔ بدون امضا پذیرفته نمی‌شود", "RAVIN_PLUGINS_ALLOW_UNSIGNED روشن است؛ آپلود محلی بدون امضا مجاز است")
        if settings.RAVIN_REGISTRY_SIGNING_KEY_FILE:
            try:
                signing.read_key_file(settings.RAVIN_REGISTRY_SIGNING_KEY_FILE)
                oks.append("فایل کلید امضای مخزن خوانا و دارای مجوز امن است")
            except signing.SigningError as exc:
                errors.append(f"کلید امضای مخزن: {exc}")
        advise(not settings.RAVIN_PLUGINS_SAFE_MODE, "حالت امن افزونه‌ها خاموش است", "حالت امن افزونه‌ها روشن است (هیچ افزونه‌ای اجرا نمی‌شود)")

        for m in oks:
            self.stdout.write(self.style.SUCCESS("  ✔ " + m))
        for m in warns:
            self.stdout.write(self.style.WARNING("  ! " + m))
        for m in errors:
            self.stdout.write(self.style.ERROR("  ✘ " + m))
        self.stdout.write(f"\n{len(oks)} موفق، {len(warns)} هشدار، {len(errors)} خطا")
        if errors:
            raise SystemExit(1)
