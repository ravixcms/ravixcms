"""«سلامت سایت»: بررسی‌های فقط‌خواندنی با سه سطح مثل Site Health وردپرس.

هر بررسی هرگز استثنا بالا نمی‌دهد؛ اگر چیزی قابل سنجش نباشد (مثلاً systemd در کانتینر) سطح ``unknown`` می‌گیرد، نه خطا.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path

from django.conf import settings
from django.utils import timezone

GOOD, RECOMMENDED, CRITICAL, UNKNOWN = "good", "recommended", "critical", "unknown"
LABELS = {GOOD: "خوب", RECOMMENDED: "پیشنهادی", CRITICAL: "بحرانی", UNKNOWN: "نامشخص"}

BACKUP_WARN_HOURS = 36
BACKUP_CRIT_HOURS = 24 * 7


@dataclass(frozen=True)
class Check:
    key: str
    group: str
    title: str
    status: str
    detail: str
    fix: str = ""

    @property
    def label(self) -> str:
        return LABELS[self.status]


def _c(key, group, title, status, detail, fix=""):
    return Check(key, group, title, status, detail, fix)


# ------------------------------------------------------------------ امنیت و HTTPS
def check_https(request=None) -> list[Check]:
    g = "امنیت و HTTPS"
    out = []
    site_url = settings.SITE_URL
    secure_request = bool(request is not None and request.is_secure())
    if secure_request or site_url.startswith("https://"):
        out.append(_c("https", g, "اتصال HTTPS", GOOD, "سایت از طریق HTTPS ارائه می‌شود."))
    elif settings.DEBUG:
        out.append(_c("https", g, "اتصال HTTPS", RECOMMENDED, "در حالت توسعه HTTPS فعال نیست.", "در سرور واقعی حتماً HTTPS را فعال کنید."))
    else:
        out.append(_c("https", g, "اتصال HTTPS", CRITICAL, "این صفحه با HTTPS باز نشده است؛ رمزها و نشست‌ها بدون رمزنگاری رد و بدل می‌شوند.",
                      "گواهی Let's Encrypt بگیرید (install.sh یا docker-compose.tls.yml) و RAVIX_SITE_URL را روی نشانی https تنظیم کنید."))
    if not settings.DEBUG:
        if getattr(settings, "SECURE_SSL_REDIRECT", False) and getattr(settings, "SESSION_COOKIE_SECURE", False):
            out.append(_c("https_cookies", g, "هدایت به HTTPS و کوکی امن", GOOD, "هدایت خودکار و کوکی «Secure» فعال است."))
        else:
            out.append(_c("https_cookies", g, "هدایت به HTTPS و کوکی امن", RECOMMENDED, "هدایت خودکار به HTTPS یا کوکی «Secure» غیرفعال است.",
                          "DJANGO_SECURE_SSL_REDIRECT را روشن کنید."))
        if getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0:
            out.append(_c("hsts", g, "HSTS", GOOD, "مرورگرها فقط با HTTPS به سایت وصل می‌شوند."))
        else:
            out.append(_c("hsts", g, "HSTS", RECOMMENDED, "هدر HSTS غیرفعال است.", "DJANGO_HSTS_SECONDS را بزرگ‌تر از صفر کنید."))
    if settings.DEBUG:
        out.append(_c("debug", g, "حالت اشکال‌زدایی (DEBUG)", CRITICAL, "DEBUG روشن است و جزئیات داخلی به بازدیدکننده نشان داده می‌شود.",
                      "در سرور واقعی از تنظیمات production استفاده کنید."))
    else:
        out.append(_c("debug", g, "حالت اشکال‌زدایی (DEBUG)", GOOD, "DEBUG خاموش است."))
    hosts = list(settings.ALLOWED_HOSTS)
    if "*" in hosts:
        out.append(_c("hosts", g, "میزبان‌های مجاز", RECOMMENDED, "ALLOWED_HOSTS شامل «*» است.", "فقط دامنهٔ خودتان را بنویسید."))
    else:
        out.append(_c("hosts", g, "میزبان‌های مجاز", GOOD, "ALLOWED_HOSTS محدود شده است."))
    return out


def check_client_ip(request=None) -> list[Check]:
    """آیا Ravix IP واقعی بازدیدکننده را می‌بیند؟ بدون آن محدودیت ورود/دیدگاه/ایمیل یا اثری ندارد یا همه را یکی می‌شمارد."""
    from apps.core import clientip

    g = "امنیت و HTTPS"
    title = "تشخیص IP بازدیدکننده"
    fix_proxy = ("پشت Nginx روی سوکت یونیکس تنظیمی لازم نیست. اگر Nginx/پراکسی روی TCP وصل می‌شود، نشانی آن را در RAVIX_TRUSTED_PROXIES بنویسید "
                 "(مثلاً 127.0.0.1) و مطمئن شوید پراکسی X-Forwarded-For را می‌فرستد (deploy/ravix-proxy.conf).")
    out = []
    bad = clientip.invalid_trusted_entries()
    if bad:
        out.append(_c("client_ip_config", g, "RAVIX_TRUSTED_PROXIES", CRITICAL, "ورودی نامعتبر: " + "، ".join(bad[:5]), "فقط IP یا CIDR بنویسید."))
    state = None
    if request is not None:
        ip, state = clientip.diagnose(request)
    if state in (None, "direct", "proxied") and request is not None:
        out.append(_c("client_ip", g, title, GOOD, f"IP این درخواست درست خوانده شد ({ip})."))
        obs = {} if state == "proxied" else clientip.observations()
    else:
        obs = clientip.observations()
        if request is not None:
            obs = {**obs, state: {"remote": request.META.get("REMOTE_ADDR") or "سوکت یونیکس"}}
    if "untrusted_proxy" in obs:
        out.append(_c("client_ip", g, title, CRITICAL,
                      f"درخواست‌ها از پراکسی {obs['untrusted_proxy'].get('remote')} می‌رسند ولی مورداعتماد نیست؛ همهٔ بازدیدکننده‌ها با یک IP شمرده می‌شوند.", fix_proxy))
    elif "no_headers" in obs:
        out.append(_c("client_ip", g, title, RECOMMENDED,
                      "از پراکسی مورداعتماد درخواست می‌رسد ولی هدر IP (X-Forwarded-For یا X-Real-IP) ندارد؛ IP قابل‌تشخیص نیست و فقط محدودیت به‌ازای نام‌کاربری اعمال می‌شود.",
                      "در پراکسی proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; را اضافه کنید."))
    elif request is None:
        out.append(_c("client_ip", g, title, UNKNOWN, "تا به حال درخواست مشکوکی دیده نشده؛ این صفحه را از طریق دامنهٔ اصلی باز کنید تا بررسی زنده انجام شود."))
    return out


# ------------------------------------------------------------------ ایمیل
def check_email() -> list[Check]:
    g = "ایمیل"
    backend = settings.EMAIL_BACKEND
    host = getattr(settings, "EMAIL_HOST", "")
    if backend.endswith(("console.EmailBackend", "locmem.EmailBackend", "dummy.EmailBackend", "filebased.EmailBackend")):
        return [_c("email", g, "ارسال ایمیل", CRITICAL if not settings.DEBUG else RECOMMENDED,
                   "ایمیل‌ها به صندوق واقعی نمی‌رسند (backend آزمایشی). بازیابی رمز و اطلاع‌رسانی دیدگاه‌ها کار نمی‌کند.",
                   "DJANGO_EMAIL_BACKEND و تنظیمات EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD را برای SMTP واقعی پر کنید.")]
    out = []
    if backend.endswith("smtp.EmailBackend") and host in ("", "localhost", "127.0.0.1") and not getattr(settings, "EMAIL_HOST_USER", ""):
        out.append(_c("email", g, "ارسال ایمیل", RECOMMENDED, "SMTP روی همین سرور و بدون ورود تنظیم شده؛ بیشتر ایمیل‌ها در اسپم می‌افتند یا رد می‌شوند.",
                      "از یک سرویس SMTP معتبر استفاده کنید و با دکمهٔ «ارسال ایمیل آزمایشی» تست کنید."))
    else:
        out.append(_c("email", g, "ارسال ایمیل", GOOD, f"SMTP تنظیم شده است ({host}). برای اطمینان، ایمیل آزمایشی بفرستید."))
    if "localhost" in settings.DEFAULT_FROM_EMAIL:
        out.append(_c("email_from", g, "نشانی فرستنده", RECOMMENDED, "DEFAULT_FROM_EMAIL هنوز مقدار پیش‌فرض (localhost) است.",
                      "نشانی واقعی دامنهٔ خودتان را بگذارید، مثلاً «سایت من <noreply@example.com>»."))
    from .models import SiteSettings

    if SiteSettings.load().admin_email:
        out.append(_c("email_admin", g, "ایمیل مدیر", GOOD, "ایمیل مدیر سایت ثبت شده است."))
    else:
        out.append(_c("email_admin", g, "ایمیل مدیر", RECOMMENDED, "ایمیل مدیر ثبت نشده؛ اطلاع‌رسانی‌ها به جایی نمی‌رسند.", "در «تنظیمات سایت» ایمیل مدیر را وارد کنید."))
    return out


# ------------------------------------------------------------------ کش، دیتابیس، دیسک
def check_cache() -> list[Check]:
    from django.core.cache import cache

    g = "کش و ذخیره‌سازی"
    backend = settings.CACHES["default"]["BACKEND"]
    key = f"ravix-health-{os.getpid()}"
    try:
        cache.set(key, "ok", 10)
        works = cache.get(key) == "ok"
        cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        return [_c("cache", g, "کش", CRITICAL, f"کش پاسخ نمی‌دهد ({type(exc).__name__}).", "اتصال Redis/پوشهٔ کش را بررسی کنید.")]
    if not works:
        return [_c("cache", g, "کش", RECOMMENDED if backend.endswith(("dummy.DummyCache",)) else CRITICAL,
                   "نوشتن و خواندن در کش جواب نداد.", "تنظیمات CACHES را بررسی کنید.")]
    if backend.endswith("locmem.LocMemCache") and not settings.DEBUG:
        return [_c("cache", g, "کش", RECOMMENDED, "کش در حافظهٔ هر پروسه است و بین ورکرها مشترک نیست (محدودیت ورود دقیق کار نمی‌کند).",
                   "RAVIX_REDIS_URL / REDIS_URL را تنظیم کنید.")]
    return [_c("cache", g, "کش", GOOD, f"کش کار می‌کند ({backend.rsplit('.', 1)[-1]}).")]


def check_database() -> list[Check]:
    from django.db import connection

    g = "کش و ذخیره‌سازی"
    out = []
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        engine = connection.vendor
        if engine == "sqlite" and not settings.DEBUG:
            out.append(_c("db", g, "پایگاه داده", RECOMMENDED, "SQLite برای سایت واقعی با چند ورکر مناسب نیست.", "به PostgreSQL مهاجرت کنید (DATABASE_URL)."))
        else:
            out.append(_c("db", g, "پایگاه داده", GOOD, f"اتصال برقرار است ({engine})."))
    except Exception as exc:  # noqa: BLE001
        out.append(_c("db", g, "پایگاه داده", CRITICAL, f"اتصال به دیتابیس ناموفق بود ({type(exc).__name__}).", "سرویس دیتابیس را بررسی کنید."))
    media = Path(settings.MEDIA_ROOT)
    if media.is_dir() and os.access(str(media), os.W_OK):
        out.append(_c("media", g, "پوشهٔ رسانه", GOOD, "پوشهٔ بارگذاری‌ها قابل نوشتن است."))
    else:
        out.append(_c("media", g, "پوشهٔ رسانه", CRITICAL, f"پوشهٔ رسانه ({media}) وجود ندارد یا قابل نوشتن نیست؛ بارگذاری تصویر کار نمی‌کند.",
                      "مالکیت/مجوز پوشه را برای کاربر سرویس درست کنید."))
    try:
        usage = shutil.disk_usage(str(media if media.exists() else settings.BASE_DIR))
        free_pct = usage.free * 100 / usage.total
        free_gb = usage.free / 1024 ** 3
        if usage.free < 200 * 1024 ** 2 or free_pct < 3:
            out.append(_c("disk", g, "فضای دیسک", CRITICAL, f"فقط {free_gb:.1f} گیگابایت آزاد است.", "فایل‌های اضافه و پشتیبان‌های قدیمی را پاک کنید یا دیسک را بزرگ‌تر کنید."))
        elif usage.free < 1024 ** 3 or free_pct < 10:
            out.append(_c("disk", g, "فضای دیسک", RECOMMENDED, f"{free_gb:.1f} گیگابایت آزاد است ({free_pct:.0f}٪).", "به‌زودی فضا را مدیریت کنید."))
        else:
            out.append(_c("disk", g, "فضای دیسک", GOOD, f"{free_gb:.1f} گیگابایت آزاد ({free_pct:.0f}٪)."))
    except OSError:
        out.append(_c("disk", g, "فضای دیسک", UNKNOWN, "فضای دیسک قابل سنجش نبود."))
    return out


# ------------------------------------------------------------------ timerها
def check_timers(now=None) -> list[Check]:
    from .management.commands.ravix_preflight import _ENABLED_OK, systemd_unit_state

    g = "کارهای زمان‌بندی‌شده"
    timers = [("ravix-publish-scheduled.timer", "انتشار نوشته‌های زمان‌بندی‌شده"), ("ravix-backup.timer", "پشتیبان‌گیری روزانه")]
    if settings.SHOP_ENABLED:
        timers.append(("ravix-expire-orders.timer", "لغو سفارش‌های پرداخت‌نشده"))
    out = []
    for unit, label in timers:
        state = systemd_unit_state(unit)
        if state is None:
            out.append(_c(unit, g, label, UNKNOWN, "systemd در دسترس نیست (کانتینر یا توسعه)؛ وضعیت این زمان‌بند از اینجا دیده نمی‌شود.",
                          "در Docker، سرویس backup و دستور publish_scheduled را بررسی کنید؛ روی سرور: systemctl list-timers."))
        elif state[0] in _ENABLED_OK and state[1] == "active":
            out.append(_c(unit, g, label, GOOD, f"{unit} فعال است."))
        else:
            out.append(_c(unit, g, label, CRITICAL, f"{unit} فعال نیست (enabled={state[0]}، active={state[1]}).",
                          f"sudo systemctl enable --now {unit}"))
    # شاهد مستقل از systemd: نوشتهٔ زمان‌بندی‌شده‌ای که ۱۵ دقیقه از موعدش گذشته ولی هنوز «منتشر» نشده
    from apps.content.models import Post, PublishStatus

    now = now or timezone.now()
    overdue = Post.objects.filter(status=PublishStatus.SCHEDULED, publish_at__lte=now - timedelta(minutes=15)).count()
    if overdue:
        out.append(_c("scheduled_overdue", g, "نوشته‌های زمان‌بندی‌شدهٔ عقب‌افتاده", RECOMMENDED,
                      f"{overdue} نوشته موعد انتشارش گذشته ولی وضعیتش هنوز «زمان‌بندی‌شده» است (به بازدیدکنندگان نمایش داده می‌شود ولی تایمر اجرا نشده).",
                      "تایمر ravix-publish-scheduled را بررسی کنید یا دستور manage.py publish_scheduled را اجرا کنید."))
    return out


# ------------------------------------------------------------------ پشتیبان
def backup_dirs() -> list[Path]:
    dirs = []
    env_dir = os.environ.get("BACKUP_DIR") or os.environ.get("RAVIX_BACKUP_DIR")
    if env_dir:
        dirs.append(Path(env_dir))
    dirs += [Path("/var/backups/ravix"), Path("/backups")]
    return dirs


def latest_backup_time(dirs=None) -> tuple[datetime | None, bool]:
    """(زمان آخرین پشتیبان کامل، آیا پوشه‌ای خوانا پیدا شد)"""
    readable = False
    best: float | None = None
    for d in (dirs if dirs is not None else backup_dirs()):
        try:
            if not d.is_dir():
                continue
            readable = True
            for p in list(d.glob("ravix-????????-??????.sha256")) + list(d.glob("ravin-????????-??????.sha256")) + [d / ".last_ok"]:
                if p.is_file():
                    best = max(best or 0, p.stat().st_mtime)
        except OSError:
            continue
    return (datetime.fromtimestamp(best, dt_tz.utc) if best else None), readable


def check_backup(dirs=None, now=None) -> list[Check]:
    g = "پشتیبان‌گیری"
    when, readable = latest_backup_time(dirs)
    if when is None:
        if not readable:
            return [_c("backup", g, "پشتیبان", UNKNOWN, "پوشهٔ پشتیبان برای برنامه قابل خواندن نیست (یا پشتیبانی تنظیم نشده).",
                       "مطمئن شوید ravix-backup.timer فعال است و متغیر BACKUP_DIR به پوشهٔ خوانا اشاره می‌کند.")]
        return [_c("backup", g, "پشتیبان", CRITICAL, "هیچ پشتیبان کاملی پیدا نشد.", "deploy/backup.sh را یک‌بار اجرا کنید و تایمر پشتیبان را فعال کنید.")]
    age = ((now or timezone.now()) - when).total_seconds() / 3600
    when_txt = timezone.localtime(when).strftime("%Y-%m-%d %H:%M")
    if age > BACKUP_CRIT_HOURS:
        return [_c("backup", g, "پشتیبان", CRITICAL, f"آخرین پشتیبان {int(age // 24)} روز پیش بوده ({when_txt}).", "تایمر پشتیبان و لاگ سرویس را بررسی کنید.")]
    if age > BACKUP_WARN_HOURS:
        return [_c("backup", g, "پشتیبان", RECOMMENDED, f"آخرین پشتیبان {int(age)} ساعت پیش بوده ({when_txt}).", "تایمر پشتیبان را بررسی کنید.")]
    return [_c("backup", g, "پشتیبان", GOOD, f"آخرین پشتیبان: {when_txt}. فایل .env جداگانه پشتیبان‌گیری شود.")]


# ------------------------------------------------------------------ نسخه‌ها
def check_versions() -> list[Check]:
    from apps.updater import versions

    g = "نسخه‌ها"
    out = []
    running = versions.running_version()
    out.append(_c("ravix_version", g, "نسخهٔ راویکس", GOOD, f"نسخهٔ در حال اجرا: {running}."))
    try:
        if versions.restart_pending():
            out.append(_c("restart", g, "راه‌اندازی دوباره", RECOMMENDED, f"روی دیسک نسخهٔ {versions.disk_version()} هست ولی نسخهٔ {running} در حال اجراست.",
                          "سرویس را ری‌استارت کنید (systemctl restart ravix)."))
    except Exception:  # noqa: BLE001
        pass
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info < (3, 11):
        out.append(_c("python", g, "پایتون", CRITICAL, f"Python {py} پشتیبانی نمی‌شود.", "به Python 3.11 یا بالاتر ارتقا دهید."))
    else:
        out.append(_c("python", g, "پایتون", GOOD, f"Python {py}."))
    import django

    out.append(_c("django", g, "Django", GOOD, f"Django {django.get_version()}."))
    try:
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
        if plan:
            out.append(_c("migrations", g, "مهاجرت‌های دیتابیس", CRITICAL, f"{len(plan)} مهاجرت اجرا نشده است.", "python manage.py migrate را اجرا کنید."))
        else:
            out.append(_c("migrations", g, "مهاجرت‌های دیتابیس", GOOD, "دیتابیس به‌روز است."))
    except Exception:  # noqa: BLE001
        out.append(_c("migrations", g, "مهاجرت‌های دیتابیس", UNKNOWN, "وضعیت مهاجرت‌ها قابل بررسی نبود."))
    try:
        bad_themes = [t["slug"] for t in versions.installed_themes() if not t["valid"]]
        if bad_themes:
            out.append(_c("themes", g, "قالب‌ها", RECOMMENDED, "قالب نامعتبر: " + "، ".join(bad_themes), "قالب را دوباره نصب یا حذف کنید."))
        else:
            out.append(_c("themes", g, "قالب‌ها", GOOD, "همهٔ قالب‌های نصب‌شده معتبرند."))
        from apps.core.version import PLUGIN_API_VERSION

        bad_plugins = [p["slug"] for p in versions.installed_plugins()
                       if p["active"] and p.get("api_version") and not versions.satisfies(PLUGIN_API_VERSION, f">={p['api_version']}")]
        if bad_plugins:
            out.append(_c("plugins", g, "افزونه‌ها", RECOMMENDED, "افزونهٔ نیازمند API جدیدتر: " + "، ".join(bad_plugins), "هسته یا افزونه را به‌روز کنید."))
        else:
            out.append(_c("plugins", g, "افزونه‌ها", GOOD, "ناسازگاری شناخته‌شده‌ای در افزونه‌های فعال نیست."))
    except Exception:  # noqa: BLE001
        out.append(_c("plugins", g, "قالب‌ها و افزونه‌ها", UNKNOWN, "نسخهٔ قالب‌ها و افزونه‌ها قابل بررسی نبود."))
    return out


GROUP_ORDER = ["امنیت و HTTPS", "ایمیل", "کش و ذخیره‌سازی", "کارهای زمان‌بندی‌شده", "پشتیبان‌گیری", "نسخه‌ها"]


def run_all(request=None) -> list[Check]:
    checks: list[Check] = []
    for fn in (lambda: check_https(request), lambda: check_client_ip(request), check_email, check_cache, check_database, check_timers, check_backup, check_versions):
        try:
            checks.extend(fn())
        except Exception as exc:  # noqa: BLE001 — یک بررسی خراب نباید کل صفحه را بیندازد
            checks.append(_c("internal", "سامانه", "خطای بررسی", UNKNOWN, f"یکی از بررسی‌ها اجرا نشد ({type(exc).__name__})."))
    return checks


def summarize(checks: list[Check]) -> dict:
    counts = {s: sum(c.status == s for c in checks) for s in (GOOD, RECOMMENDED, CRITICAL, UNKNOWN)}
    scored = counts[GOOD] + counts[RECOMMENDED] + counts[CRITICAL]
    score = round((counts[GOOD] + counts[RECOMMENDED] * 0.5) * 100 / scored) if scored else 100
    if counts[CRITICAL]:
        verdict, tone = "مشکل‌های بحرانی دارید", CRITICAL
    elif counts[RECOMMENDED]:
        verdict, tone = "سایت خوب کار می‌کند، چند بهبود پیشنهاد می‌شود", RECOMMENDED
    else:
        verdict, tone = "سایت شما سالم است", GOOD
    return {"counts": counts, "score": score, "verdict": verdict, "tone": tone}
