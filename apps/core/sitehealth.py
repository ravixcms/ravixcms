"""«سلامت سایت»: بررسی‌های فقط‌خواندنی با سه سطح مثل Site Health وردپرس.

هر بررسی هرگز استثنا بالا نمی‌دهد؛ اگر چیزی قابل سنجش نباشد (مثلاً systemd در کانتینر) سطح ``unknown`` می‌گیرد، نه خطا.
"""
from __future__ import annotations

from django.utils.translation import gettext, gettext_lazy
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
LABELS = {GOOD: gettext_lazy("Good"), RECOMMENDED: gettext_lazy("Recommended"), CRITICAL: gettext_lazy("Critical"), UNKNOWN: gettext_lazy("Unknown")}

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
    g = gettext("Security and HTTPS")
    out = []
    site_url = settings.SITE_URL
    secure_request = bool(request is not None and request.is_secure())
    if secure_request or site_url.startswith("https://"):
        out.append(_c("https", g, gettext("HTTPS connection"), GOOD, gettext("The site is served over HTTPS.")))
    elif settings.DEBUG:
        out.append(_c("https", g, gettext("HTTPS connection"), RECOMMENDED, gettext("HTTPS is not enabled in development mode."), gettext("Be sure to enable HTTPS on a real server.")))
    else:
        out.append(_c("https", g, gettext("HTTPS connection"), CRITICAL, gettext("This page was not opened over HTTPS; passwords and sessions are exchanged without encryption."),
                      gettext("Get a Let’s Encrypt certificate (install.sh or docker-compose.tls.yml) and set RAVIX_SITE_URL to an https address.")))
    if not settings.DEBUG:
        if getattr(settings, "SECURE_SSL_REDIRECT", False) and getattr(settings, "SESSION_COOKIE_SECURE", False):
            out.append(_c("https_cookies", g, gettext("HTTPS redirect and secure cookie"), GOOD, gettext("Automatic redirect and the “Secure” cookie are enabled.")))
        else:
            out.append(_c("https_cookies", g, gettext("HTTPS redirect and secure cookie"), RECOMMENDED, gettext("Automatic redirect to HTTPS or the “Secure” cookie is disabled."),
                          gettext("Turn on DJANGO_SECURE_SSL_REDIRECT.")))
        if getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0:
            out.append(_c("hsts", g, "HSTS", GOOD, gettext("Browsers connect to the site over HTTPS only.")))
        else:
            out.append(_c("hsts", g, "HSTS", RECOMMENDED, gettext("The HSTS header is disabled."), gettext("Set DJANGO_HSTS_SECONDS to a value greater than zero.")))
    if settings.DEBUG:
        out.append(_c("debug", g, gettext("Debug mode (DEBUG)"), CRITICAL, gettext("DEBUG is on and internal details are shown to visitors."),
                      gettext("On a real server use the production settings.")))
    else:
        out.append(_c("debug", g, gettext("Debug mode (DEBUG)"), GOOD, gettext("DEBUG is off.")))
    hosts = list(settings.ALLOWED_HOSTS)
    if "*" in hosts:
        out.append(_c("hosts", g, gettext("Allowed hosts"), RECOMMENDED, gettext("ALLOWED_HOSTS contains “*”."), gettext("Write only your own domain.")))
    else:
        out.append(_c("hosts", g, gettext("Allowed hosts"), GOOD, gettext("ALLOWED_HOSTS is restricted.")))
    return out


def check_client_ip(request=None) -> list[Check]:
    """آیا Ravix IP واقعی بازدیدکننده را می‌بیند؟ بدون آن محدودیت ورود/دیدگاه/ایمیل یا اثری ندارد یا همه را یکی می‌شمارد."""
    from apps.core import clientip

    g = gettext("Security and HTTPS")
    title = gettext("Visitor IP detection")
    fix_proxy = (gettext("Behind Nginx on a unix socket no setting is needed. If Nginx/a proxy connects over TCP, put its address in RAVIX_TRUSTED_PROXIES (e.g. 127.0.0.1) and make sure the proxy sends X-Forwarded-For (deploy/ravix-proxy.conf)."))
    out = []
    bad = clientip.invalid_trusted_entries()
    if bad:
        out.append(_c("client_ip_config", g, "RAVIX_TRUSTED_PROXIES", CRITICAL, (gettext("Invalid entry: %(items)s") % {"items": gettext(", ").join(bad[:5])}), gettext("Write only an IP or CIDR.")))
    state = None
    if request is not None:
        ip, state = clientip.diagnose(request)
    if state in (None, "direct", "proxied") and request is not None:
        out.append(_c("client_ip", g, title, GOOD, (gettext("The IP of this request was read correctly (%(ip)s).") % {"ip": ip})))
        obs = {} if state == "proxied" else clientip.observations()
    else:
        obs = clientip.observations()
        if request is not None:
            obs = {**obs, state: {"remote": request.META.get("REMOTE_ADDR") or gettext("unix socket")}}
    if "untrusted_proxy" in obs:
        out.append(_c("client_ip", g, title, CRITICAL,
                      (gettext("Requests arrive from proxy %(value)s but it is not trusted; all visitors are counted as one IP.") % {"value": obs['untrusted_proxy'].get('remote')}), fix_proxy))
    elif "no_headers" in obs:
        out.append(_c("client_ip", g, title, RECOMMENDED,
                      gettext("Requests arrive from a trusted proxy but have no IP header (X-Forwarded-For or X-Real-IP); the IP cannot be determined and only the per-username limit is applied."),
                      gettext("Add proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; to the proxy.")))
    elif request is None:
        out.append(_c("client_ip", g, title, UNKNOWN, gettext("No suspicious request has been seen so far; open this page through the main domain so a live check can be done.")))
    return out


# ------------------------------------------------------------------ ایمیل
def check_email() -> list[Check]:
    g = gettext("Email")
    backend = settings.EMAIL_BACKEND
    host = getattr(settings, "EMAIL_HOST", "")
    if backend.endswith(("console.EmailBackend", "locmem.EmailBackend", "dummy.EmailBackend", "filebased.EmailBackend")):
        return [_c("email", g, gettext("Email sending"), CRITICAL if not settings.DEBUG else RECOMMENDED,
                   gettext("Emails do not reach real inboxes (test backend). Password reset and comment notifications do not work."),
                   gettext("Fill in DJANGO_EMAIL_BACKEND and the EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD settings for a real SMTP."))]
    out = []
    if backend.endswith("smtp.EmailBackend") and host in ("", "localhost", "127.0.0.1") and not getattr(settings, "EMAIL_HOST_USER", ""):
        out.append(_c("email", g, gettext("Email sending"), RECOMMENDED, gettext("SMTP is configured on this same server without authentication; most emails land in spam or are rejected."),
                      gettext("Use a reputable SMTP service and test it with the “Send test email” button.")))
    else:
        out.append(_c("email", g, gettext("Email sending"), GOOD, (gettext("SMTP is configured (%(host)s). To be sure, send a test email.") % {"host": host})))
    if "localhost" in settings.DEFAULT_FROM_EMAIL:
        out.append(_c("email_from", g, gettext("Sender address"), RECOMMENDED, gettext("DEFAULT_FROM_EMAIL still has the default value (localhost)."),
                      gettext("Set a real address on your own domain, e.g. “My Site <noreply@example.com>”.")))
    from .models import SiteSettings

    if SiteSettings.load().admin_email:
        out.append(_c("email_admin", g, gettext("Admin email"), GOOD, gettext("The site admin email is set.")))
    else:
        out.append(_c("email_admin", g, gettext("Admin email"), RECOMMENDED, gettext("No admin email is set; notifications go nowhere."), gettext("Enter the admin email in “Site settings”.")))
    return out


# ------------------------------------------------------------------ کش، دیتابیس، دیسک
def check_cache() -> list[Check]:
    from django.core.cache import cache

    g = gettext("Cache and storage")
    backend = settings.CACHES["default"]["BACKEND"]
    key = f"ravix-health-{os.getpid()}"
    try:
        cache.set(key, "ok", 10)
        works = cache.get(key) == "ok"
        cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        return [_c("cache", g, gettext("Cache"), CRITICAL, (gettext("The cache is not responding (%(name)s).") % {"name": type(exc).__name__}), gettext("Check the Redis connection / cache folder."))]
    if getattr(cache, "broken", False):  # SafeFileBasedCache never raises; it records that its folder cannot be written
        return [_c("cache", g, gettext("Cache"), CRITICAL, gettext("The cache folder is missing or not writable for the service user."),
                   gettext("Create the folder and give it to the service user (CacheDirectory=ravix in the systemd unit, or RAVIX_CACHE_DIR)."))]
    if not works:
        return [_c("cache", g, gettext("Cache"), RECOMMENDED if backend.endswith(("dummy.DummyCache",)) else CRITICAL,
                   gettext("Writing to and reading from the cache did not work."), gettext("Check the CACHES setting."))]
    if backend.endswith("locmem.LocMemCache") and not settings.DEBUG:
        return [_c("cache", g, gettext("Cache"), RECOMMENDED, gettext("The cache is per-process memory and is not shared between workers (precise login rate limiting does not work)."),
                   gettext("Set RAVIX_REDIS_URL / REDIS_URL."))]
    return [_c("cache", g, gettext("Cache"), GOOD, (gettext("The cache is working (%(value)s).") % {"value": backend.rsplit('.', 1)[-1]}))]


def check_database() -> list[Check]:
    from django.db import connection

    g = gettext("Cache and storage")
    out = []
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        engine = connection.vendor
        if engine == "sqlite" and not settings.DEBUG:
            out.append(_c("db", g, gettext("Database"), RECOMMENDED, gettext("SQLite is not suitable for a real site with several workers."), gettext("Migrate to PostgreSQL (DATABASE_URL).")))
        else:
            out.append(_c("db", g, gettext("Database"), GOOD, (gettext("The connection is established (%(engine)s).") % {"engine": engine})))
    except Exception as exc:  # noqa: BLE001
        out.append(_c("db", g, gettext("Database"), CRITICAL, (gettext("Connecting to the database failed (%(name)s).") % {"name": type(exc).__name__}), gettext("Check the database service.")))
    media = Path(settings.MEDIA_ROOT)
    if media.is_dir() and os.access(str(media), os.W_OK):
        out.append(_c("media", g, gettext("Media folder"), GOOD, gettext("The uploads folder is writable.")))
    else:
        out.append(_c("media", g, gettext("Media folder"), CRITICAL, (gettext("The media folder (%(media)s) does not exist or is not writable; image upload does not work.") % {"media": media}),
                      gettext("Fix the folder ownership/permissions for the service user.")))
    try:
        usage = shutil.disk_usage(str(media if media.exists() else settings.BASE_DIR))
        free_pct = usage.free * 100 / usage.total
        free_gb = usage.free / 1024 ** 3
        if usage.free < 200 * 1024 ** 2 or free_pct < 3:
            out.append(_c("disk", g, gettext("Disk space"), CRITICAL, (gettext("Only %(free_gb)s GB is free.") % {"free_gb": format(free_gb, '.1f')}), gettext("Delete extra files and old backups or enlarge the disk.")))
        elif usage.free < 1024 ** 3 or free_pct < 10:
            out.append(_c("disk", g, gettext("Disk space"), RECOMMENDED, (gettext("%(free_gb)s GB is free (%(free_pct)s%%).") % {"free_gb": format(free_gb, '.1f'), "free_pct": format(free_pct, '.0f')}), gettext("Manage disk space soon.")))
        else:
            out.append(_c("disk", g, gettext("Disk space"), GOOD, (gettext("%(free_gb)s GB free (%(free_pct)s%%).") % {"free_gb": format(free_gb, '.1f'), "free_pct": format(free_pct, '.0f')})))
    except OSError:
        out.append(_c("disk", g, gettext("Disk space"), UNKNOWN, gettext("Disk space could not be measured.")))
    return out


# ------------------------------------------------------------------ timerها
def check_code_dirs() -> list[Check]:
    """Plugin/theme code folders are read-only for the service on purpose (root:ravix 750), but the service must still be able to read them."""
    from . import fsaccess

    g = gettext("Cache and storage")
    dirs = [*getattr(settings, "RAVIX_PLUGIN_DIRS", []), *getattr(settings, "RAVIX_THEME_DIRS", [])]
    bad = []
    for d in dirs:
        reason = fsaccess.list_error(d)
        if reason and reason != "missing":
            bad.append(str(d))
    if bad:
        return [_c("code_dirs", g, gettext("Code folders"), CRITICAL,
                   (gettext("The web service cannot read these folders: %(dirs)s. The plugin list and plugins do not work.") % {"dirs": gettext(", ").join(bad)}),
                   gettext("Add the folder’s group to the service (SupplementaryGroups=ravix in ravix.service, then daemon-reload and restart), or run install.sh again."))]
    return [_c("code_dirs", g, gettext("Code folders"), GOOD, gettext("The plugin and theme folders are readable by the web service."))]


def check_timers(now=None) -> list[Check]:
    from .management.commands.ravix_preflight import _ENABLED_OK, systemd_unit_state

    g = gettext("Scheduled jobs")
    timers = [("ravix-publish-scheduled.timer", gettext("Publishing scheduled posts")), ("ravix-backup.timer", gettext("Daily backup"))]
    from . import modules

    if settings.SHOP_ENABLED and modules.is_enabled("shop"):
        timers.append(("ravix-expire-orders.timer", gettext("Cancelling unpaid orders")))
    out = []
    for unit, label in timers:
        state = systemd_unit_state(unit)
        if state is None:
            out.append(_c(unit, g, label, UNKNOWN, gettext("systemd is not available (container or development); the status of this scheduler cannot be seen from here."),
                          gettext("In Docker, check the backup service and the publish_scheduled command; on a server: systemctl list-timers.")))
        elif state[0] in _ENABLED_OK and state[1] == "active":
            out.append(_c(unit, g, label, GOOD, (gettext("%(unit)s is active.") % {"unit": unit})))
        else:
            out.append(_c(unit, g, label, CRITICAL, (gettext("%(unit)s is not active (enabled=%(state)s, active=%(state2)s).") % {"unit": unit, "state": state[0], "state2": state[1]}),
                          f"sudo systemctl enable --now {unit}"))
    # شاهد مستقل از systemd: نوشتهٔ زمان‌بندی‌شده‌ای که ۱۵ دقیقه از موعدش گذشته ولی هنوز «منتشر» نشده
    from apps.content.models import Post, PublishStatus

    now = now or timezone.now()
    overdue = Post.objects.filter(status=PublishStatus.SCHEDULED, publish_at__lte=now - timedelta(minutes=15)).count()
    if overdue:
        out.append(_c("scheduled_overdue", g, gettext("Overdue scheduled posts"), RECOMMENDED,
                      (gettext("%(overdue)s posts are past their publish time but are still “scheduled” (they are shown to visitors but the timer has not run).") % {"overdue": overdue}),
                      gettext("Check the ravix-publish-scheduled timer or run manage.py publish_scheduled.")))
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
    g = gettext("Backups")
    when, readable = latest_backup_time(dirs)
    if when is None:
        if not readable:
            return [_c("backup", g, gettext("Backup"), UNKNOWN, gettext("The backup folder is not readable by the application (or backups are not configured)."),
                       gettext("Make sure ravix-backup.timer is active and BACKUP_DIR points to a readable folder."))]
        return [_c("backup", g, gettext("Backup"), CRITICAL, gettext("No complete backup was found."), gettext("Run deploy/backup.sh once and enable the backup timer."))]
    age = ((now or timezone.now()) - when).total_seconds() / 3600
    when_txt = timezone.localtime(when).strftime("%Y-%m-%d %H:%M")
    if age > BACKUP_CRIT_HOURS:
        return [_c("backup", g, gettext("Backup"), CRITICAL, (gettext("The last backup was %(value)s days ago (%(when_txt)s).") % {"value": int(age // 24), "when_txt": when_txt}), gettext("Check the backup timer and the service log."))]
    if age > BACKUP_WARN_HOURS:
        return [_c("backup", g, gettext("Backup"), RECOMMENDED, (gettext("The last backup was %(age)s hours ago (%(when_txt)s).") % {"age": int(age), "when_txt": when_txt}), gettext("Check the backup timer."))]
    return [_c("backup", g, gettext("Backup"), GOOD, (gettext("Last backup: %(when_txt)s. Back up the .env file separately.") % {"when_txt": when_txt}))]


# ------------------------------------------------------------------ نسخه‌ها
def check_versions() -> list[Check]:
    from apps.updater import versions

    g = gettext("Versions")
    out = []
    running = versions.running_version()
    out.append(_c("ravix_version", g, gettext("Ravix version"), GOOD, (gettext("Running version: %(running)s.") % {"running": running})))
    try:
        if versions.restart_pending():
            out.append(_c("restart", g, gettext("Restart"), RECOMMENDED, (gettext("Version %(disk_version)s is on disk but version %(running)s is running.") % {"disk_version": versions.disk_version(), "running": running}),
                          gettext("Restart the service (systemctl restart ravix).")))
    except Exception:  # noqa: BLE001
        pass
    py = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info < (3, 11):
        out.append(_c("python", g, gettext("Python"), CRITICAL, (gettext("Python %(py)s is not supported.") % {"py": py}), gettext("Upgrade to Python 3.11 or later.")))
    else:
        out.append(_c("python", g, gettext("Python"), GOOD, f"Python {py}."))
    import django

    out.append(_c("django", g, "Django", GOOD, f"Django {django.get_version()}."))
    try:
        from apps.updater import sources as upd_sources

        state = upd_sources.source_state()
        title = gettext("Update source")
        if state == "no_key":
            out.append(_c("update_source", g, title, UNKNOWN, gettext("The official update source is disabled until a trusted public key is configured, because no package can be verified without one. This is not an error; new versions are simply not checked yet."),
                          gettext("Nothing to do unless you want update checks: add the publisher’s public key (RAVIX_TRUSTED_PUBLIC_KEYS).")))
        elif state == "none":
            out.append(_c("update_source", g, title, UNKNOWN, gettext("No update source is configured, so new versions are not checked."), gettext("Set RAVIX_UPDATE_URL if you want update checks.")))
        else:
            out.append(_c("update_source", g, title, GOOD, gettext("The update source is configured and a trusted public key is present.")))
    except Exception:  # noqa: BLE001
        pass
    try:
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
        if plan:
            out.append(_c("migrations", g, gettext("Database migrations"), CRITICAL, (gettext("%(plan_count)s migrations have not been applied.") % {"plan_count": len(plan)}), gettext("Run python manage.py migrate.")))
        else:
            out.append(_c("migrations", g, gettext("Database migrations"), GOOD, gettext("The database is up to date.")))
    except Exception:  # noqa: BLE001
        out.append(_c("migrations", g, gettext("Database migrations"), UNKNOWN, gettext("The migration status could not be checked.")))
    try:
        bad_themes = [t["slug"] for t in versions.installed_themes() if not t["valid"]]
        if bad_themes:
            out.append(_c("themes", g, gettext("Themes"), RECOMMENDED, (gettext("Invalid theme: %(bad_themes)s") % {"bad_themes": gettext(", ").join(bad_themes)}), gettext("Reinstall or remove the theme.")))
        else:
            out.append(_c("themes", g, gettext("Themes"), GOOD, gettext("All installed themes are valid.")))
        from apps.core.version import PLUGIN_API_VERSION

        bad_plugins = [p["slug"] for p in versions.installed_plugins()
                       if p["active"] and p.get("api_version") and not versions.satisfies(PLUGIN_API_VERSION, f">={p['api_version']}")]
        if bad_plugins:
            out.append(_c("plugins", g, gettext("Plugins"), RECOMMENDED, (gettext("Plugin requiring a newer API: %(bad_plugins)s") % {"bad_plugins": gettext(", ").join(bad_plugins)}), gettext("Update the core or the plugin.")))
        else:
            out.append(_c("plugins", g, gettext("Plugins"), GOOD, gettext("There are no known incompatibilities among the active plugins.")))
    except Exception:  # noqa: BLE001
        out.append(_c("plugins", g, gettext("Themes and plugins"), UNKNOWN, gettext("The version of themes and plugins could not be checked.")))
    return out


def check_content() -> list[Check]:
    """Pages/posts whose address is a language code (``/de/`` is the German home page, so such a page cannot be reached)."""
    from apps.content import slug_audit
    from apps.content.models import Page, Post

    g = gettext("Content")
    title = gettext("Page and post addresses")
    pages, posts = slug_audit.count(Page, Post)
    if not pages and not posts:
        return [_c("language_slugs", g, title, GOOD, gettext("No page or post uses a language code as its address."))]
    example = (slug_audit.find(Page, Post)[0][1]).slug
    detail = gettext("%(pages)s page(s) and %(posts)s post(s) use a language code (for example “%(example)s”) as their address.") % {"pages": pages, "posts": posts, "example": example}
    return [_c("language_slugs", g, title, CRITICAL if pages else RECOMMENDED, detail,
               gettext("Run python manage.py ravix_fix_slugs: it renames the address, adds a 301 redirect and logs the change."))]


GROUP_ORDER = [gettext_lazy("Security and HTTPS"), gettext_lazy("Email"), gettext_lazy("Cache and storage"), gettext_lazy("Scheduled jobs"), gettext_lazy("Backups"), gettext_lazy("Versions"), gettext_lazy("Content")]


def run_all(request=None) -> list[Check]:
    checks: list[Check] = []
    for fn in (lambda: check_https(request), lambda: check_client_ip(request), check_email, check_cache, check_database, check_code_dirs, check_timers, check_backup, check_versions, check_content):
        try:
            checks.extend(fn())
        except Exception as exc:  # noqa: BLE001 — یک بررسی خراب نباید کل صفحه را بیندازد
            checks.append(_c("internal", gettext("System"), gettext("Check error"), UNKNOWN, (gettext("One of the checks did not run (%(name)s).") % {"name": type(exc).__name__})))
    return checks


def summarize(checks: list[Check]) -> dict:
    counts = {s: sum(c.status == s for c in checks) for s in (GOOD, RECOMMENDED, CRITICAL, UNKNOWN)}
    scored = counts[GOOD] + counts[RECOMMENDED] + counts[CRITICAL]
    score = round((counts[GOOD] + counts[RECOMMENDED] * 0.5) * 100 / scored) if scored else 100
    if counts[CRITICAL]:
        verdict, tone = gettext("You have critical problems"), CRITICAL
    elif counts[RECOMMENDED]:
        verdict, tone = gettext("The site works well; a few improvements are suggested"), RECOMMENDED
    else:
        verdict, tone = gettext("Your site is healthy"), GOOD
    return {"counts": counts, "score": score, "verdict": verdict, "tone": tone}
