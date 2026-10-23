from apps.core.clilang import cli_gettext
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
    help = cli_gettext("Checks deployment readiness (configuration, security, migrations, paths). Exits with code 1 on errors.")

    def add_arguments(self, parser):
        parser.add_argument("--ci", action="store_true", default=False,
                            help=cli_gettext("CI mode (also RAVIX_CI=1): checks that only make sense on a real production server (systemd timers, folder existence and permissions) are reported as warnings instead of errors. Security and configuration checks stay strict."))

    def handle(self, *args, **options):
        errors, warns, oks = [], [], []
        ci = bool(options.get("ci")) or os.environ.get("RAVIX_CI", "").strip().lower() in {"1", "true", "yes", "on"}

        def need(cond, ok, bad):
            (oks if cond else errors).append(ok if cond else bad)

        def advise(cond, ok, bad):
            (oks if cond else warns).append(ok if cond else bad)

        def server(cond, ok, bad):
            """A check about the production server itself (systemd, folders and their ownership): an error on a real server, a warning in CI mode."""
            (oks if cond else (warns if ci else errors)).append(ok if cond else bad)

        if ci:
            warns.append(cli_gettext("CI mode: server-only checks (systemd timers, folders and their permissions) are reported as warnings; never use this mode to approve a real server"))

        need(not settings.DEBUG, cli_gettext("DEBUG is off"), cli_gettext("DEBUG is on"))
        need(len(settings.SECRET_KEY) >= 32 and "insecure" not in settings.SECRET_KEY.lower(), cli_gettext("SECRET_KEY is strong"), cli_gettext("SECRET_KEY is weak or the default"))
        need("*" not in settings.ALLOWED_HOSTS and settings.ALLOWED_HOSTS, cli_gettext("ALLOWED_HOSTS is set"), cli_gettext("ALLOWED_HOSTS is empty or *"))
        need(connection.vendor == "postgresql", cli_gettext("The database is PostgreSQL"), cli_gettext("The database is %(a1)s (PostgreSQL is required for production)") % {"a1": connection.vendor})
        need(getattr(settings, "SECURE_SSL_REDIRECT", False), cli_gettext("HTTPS redirect is enabled"), cli_gettext("SECURE_SSL_REDIRECT is off"))
        need(settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE, cli_gettext("Cookies are Secure"), cli_gettext("Cookies are not Secure"))
        need(getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0, cli_gettext("HSTS is enabled"), cli_gettext("HSTS is off"))
        advise(bool(settings.CSRF_TRUSTED_ORIGINS), cli_gettext("CSRF_TRUSTED_ORIGINS is set"), cli_gettext("CSRF_TRUSTED_ORIGINS is empty (usually required behind HTTPS with a domain)"))
        advise(bool(settings.SITE_URL), cli_gettext("SITE_URL is set (absolute sitemap/canonical URLs)"), cli_gettext("SITE_URL is empty; absolute URLs are built from the request Host"))
        advise("console" not in settings.EMAIL_BACKEND, cli_gettext("Real email is configured"), cli_gettext("EMAIL_BACKEND is the console one; no email is sent"))
        from apps.content import slug_audit
        from apps.content.models import Page, Post

        n_pages, n_posts = slug_audit.count(Page, Post)
        advise(not (n_pages or n_posts), cli_gettext("No page or post has a language code as its slug"),
               cli_gettext("%(n_pages)s pages and %(n_posts)s posts have a slug equal to a language code (for example /de/ is now the German home page). Run: python manage.py ravix_fix_slugs") % {"n_pages": n_pages, "n_posts": n_posts})
        media = str(settings.MEDIA_ROOT)
        server(os.path.isdir(media) and os.access(media, os.W_OK), cli_gettext("The media folder is writable"), cli_gettext("The media folder (%(media)s) does not exist or is not writable (create it: mkdir -p %(media)s and give ownership to the service user)") % {"media": media})
        advise(os.path.isdir(settings.STATIC_ROOT) and os.listdir(settings.STATIC_ROOT), cli_gettext("collectstatic has been run"), cli_gettext("collectstatic has not been run"))
        # --- کش (محدودسازی ورود از کش می‌خواند؛ کش خراب = خطای ۵۰۰ در ورود) ---
        from django.core.cache import caches

        cache_conf = settings.CACHES.get("default", {})
        if cache_conf.get("BACKEND", "").endswith("FileBasedCache"):
            cdir = str(cache_conf.get("LOCATION", ""))
            server(os.path.isdir(cdir) and os.access(cdir, os.W_OK), cli_gettext("The cache folder (%(cdir)s) is writable") % {"cdir": cdir},
                 cli_gettext("The cache folder (%(cdir)s) does not exist or is not writable for the service user (use CacheDirectory=ravix in ravix.service)") % {"cdir": cdir})
        try:
            probe = "ravix-preflight-probe"
            caches["default"].set(probe, "1", 10)
            ok_cache = caches["default"].get(probe) == "1"
            caches["default"].delete(probe)
        except Exception as exc:  # noqa: BLE001 - هر خطای کش
            ok_cache, cache_err = False, f"{type(exc).__name__}: {exc}"
        else:
            cache_err = cli_gettext("The written value could not be read back")
        need(ok_cache, cli_gettext("The main cache works (a test write/read succeeded)"), cli_gettext("The main cache does not work (%(cache_err)s). Panel sign-in falls back to the database cache table without a healthy cache and logs a warning") % {"cache_err": cache_err})
        if "throttle_fallback" in settings.CACHES:
            try:
                from apps.core.models import ThrottleCacheEntry

                ThrottleCacheEntry.objects.count()
                ok_fb = True
            except Exception:  # noqa: BLE001 - پیش از migrate
                ok_fb = False
            advise(ok_fb, cli_gettext("The fallback cache table (ravin_cache) exists in the database"), cli_gettext("The fallback cache table (ravin_cache) is missing; run migrate"))
        # --- IP کلاینت (محدودیت ورود/دیدگاه/ایمیل به آن وابسته است) ---
        from apps.core import clientip

        bad_proxies = clientip.invalid_trusted_entries()
        need(not bad_proxies, cli_gettext("RAVIX_TRUSTED_PROXIES is valid"), cli_gettext("RAVIX_TRUSTED_PROXIES contains an invalid entry: %(bad_proxies)s") % {"bad_proxies": bad_proxies})
        if os.environ.get("RAVIX_TRUST_PROXY_HEADERS"):
            warns.append(cli_gettext("RAVIX_TRUST_PROXY_HEADERS has been removed and has no effect; nothing is needed behind a Unix socket, and for a proxy over TCP use RAVIX_TRUSTED_PROXIES"))
        seen = clientip.observations()
        if "untrusted_proxy" in seen:
            errors.append(cli_gettext("Requests arrive from the proxy %(a1)s which is not trusted; all visitors are counted as one IP. Add it to RAVIX_TRUSTED_PROXIES (or put Gunicorn on a Unix socket)") % {"a1": seen['untrusted_proxy'].get('remote')})
        elif "no_headers" in seen:
            warns.append(cli_gettext("The trusted proxy sends no IP header; the client IP cannot be determined (only per-username throttling applies)"))
        else:
            oks.append(cli_gettext("Client IP detection: no problem seen (for a live check open the “Site health” page through the domain)"))
        from apps.core.deprecation import LEGACY_ENV_USED
        if LEGACY_ENV_USED:
            warns.append(cli_gettext("Old environment variables are in use: ") + cli_gettext(", ").join(sorted(LEGACY_ENV_USED))
                         + cli_gettext(" — rename them to RAVIX_* (they are removed in 2.0; docs/upgrade.md)"))
        static_root = str(settings.STATIC_ROOT)
        server(os.path.isdir(static_root) and os.access(static_root, os.R_OK), cli_gettext("STATIC_ROOT exists and is readable"), cli_gettext("STATIC_ROOT (%(static_root)s) does not exist or is not readable; run collectstatic") % {"static_root": static_root})
        # --- تایمرهای systemd: بدون آن‌ها نوشتهٔ زمان‌بندی‌شده منتشر نمی‌شود و پشتیبان گرفته نمی‌شود ---
        timers = [("ravix-publish-scheduled.timer", cli_gettext("Publishing scheduled posts")), ("ravix-backup.timer", cli_gettext("Daily backup"))]
        if settings.SHOP_ENABLED:
            timers.append(("ravix-expire-orders.timer", cli_gettext("Cancelling unpaid orders")))
        unavailable = False
        for unit, label in timers:
            state = systemd_unit_state(unit)
            if state is not None and state[0] not in _ENABLED_OK:
                old_unit = "ravin-" + unit[len("ravix-"):]  # نصب قدیمی: تا 2.0 پذیرفته می‌شود
                old_state = systemd_unit_state(old_unit)
                if old_state is not None and old_state[0] in _ENABLED_OK and old_state[1] == "active":
                    warns.append(cli_gettext("The old timer %(old_unit)s is active; migrate to %(unit)s (docs/upgrade.md). The old name is removed in 2.0") % {"old_unit": old_unit, "unit": unit})
                    continue
            if state is None:
                unavailable = True
                continue
            enabled, active = state
            server(enabled in _ENABLED_OK and active == "active", cli_gettext("The timer %(unit)s (%(label)s) is active") % {"unit": unit, "label": label},
                 cli_gettext("The timer %(unit)s (%(label)s) is not active (enabled=%(enabled)s, active=%(active)s): sudo cp deploy/%(a5)s.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now %(unit)s") % {"unit": unit, "label": label, "enabled": enabled, "active": active, "a5": unit[:-6]})
        if unavailable:
            warns.append(cli_gettext("The state of the systemd timers (scheduled publishing, backup, orders) could not be checked; run again on a server with systemd or look manually with “systemctl list-timers”"))
        from apps.plugins import signing

        install_dir = Path(settings.RAVIX_PLUGIN_INSTALL_DIR)
        for d in settings.RAVIX_PLUGIN_DIRS:
            if settings.RAVIX_PLUGIN_WEB_INSTALL and Path(d) == install_dir:
                continue  # پوشهٔ نصب خودکار عمداً قابل‌نوشتن است
            advise(not os.access(str(d), os.W_OK) or os.geteuid() == 0, cli_gettext("The plugin folder %(d)s is read-only for this user") % {"d": d}, cli_gettext("The plugin folder %(d)s is writable for the service user (recommended: read-only)") % {"d": d})
        # --- به‌روزرسانی یکپارچه ---
        from apps.updater import versions as upd_versions
        from apps.updater.models import UpdateRun

        try:
            stuck = UpdateRun.objects.filter(status="needs_recovery").count()
            need(not stuck, cli_gettext("No upgrade needs recovery"), cli_gettext("%(stuck)s upgrades need manual recovery (panel → Updates)") % {"stuck": stuck})
        except Exception:  # noqa: BLE001 - پیش از migrate
            pass
        advise(not upd_versions.restart_pending(), cli_gettext("The code on disk is the same as the running process"), cli_gettext("The code on disk is newer than the running process; restart the service"))
        advise(bool(settings.RAVIX_UPDATE_URL or settings.RAVIX_UPDATE_DIR), cli_gettext("An update source is configured"), cli_gettext("No update source (RAVIX_UPDATE_URL) is configured; checking for new versions is disabled"))
        if settings.RAVIX_UPDATE_URL:
            need(settings.RAVIX_UPDATE_URL.startswith("https://"), cli_gettext("The update source is HTTPS"), cli_gettext("RAVIX_UPDATE_URL must be https"))
        if settings.RAVIX_UPDATE_URL or settings.RAVIX_UPDATE_DIR:
            advise(bool(signing.trusted_keys()), cli_gettext("A trusted public key for package signatures is configured"), cli_gettext("There is no trusted key (RAVIX_TRUSTED_PUBLIC_KEYS); no update package is accepted"))
        advise(not settings.RAVIX_UPDATE_WEB_APPLY, cli_gettext("Applying upgrades from the web is off (recommended)"), cli_gettext("RAVIX_UPDATE_WEB_APPLY is on; the web service must have write access to the code and the database"))
        try:
            plan = MigrationExecutor(connection).migration_plan(MigrationExecutor(connection).loader.graph.leaf_nodes())
            need(not plan, cli_gettext("All migrations have been applied"), cli_gettext("%(a1)s migrations have not been applied; run migrate") % {"a1": len(plan)})
        except Exception as exc:  # noqa: BLE001
            errors.append(cli_gettext("Database connection failed: %(exc)s") % {"exc": exc})
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
                errors.append(cli_gettext("The test gateway is allowed in production; real customers must not pay with it"))
            elif test_on and not settings.DEBUG:
                errors.append(cli_gettext("The test gateway is enabled in the panel; disable it in production"))
            elif real:
                oks.append(cli_gettext("Active payment gateway(s): ") + cli_gettext(", ").join(sorted({r.gateway for r in real})))
                tm = [r.gateway for r in real if r.test_mode]
                if tm:
                    warns.append(cli_gettext("The gateway is active in test mode: ") + cli_gettext(", ").join(tm))
            else:
                warns.append(cli_gettext("The shop is on but no real gateway is active; ordering is disabled (RAVIX_SHOP_ENABLED=false turns it off completely)"))
            if has_secrets or real:
                need(gw_vault.key_configured(), cli_gettext("The gateway secrets encryption key (RAVIX_SECRETS_KEY) is set"), cli_gettext("RAVIX_SECRETS_KEY is not set; gateway keys cannot be stored or read safely"))
            else:
                advise(gw_vault.key_configured(), cli_gettext("The gateway secrets encryption key is set"), cli_gettext("RAVIX_SECRETS_KEY is not set (required before adding a gateway key)"))
        if settings.SHOP_ENABLED:
            priv = Path(settings.SHOP_PRIVATE_ROOT).resolve()
            media_root = Path(settings.MEDIA_ROOT).resolve()
            inside_media = priv == media_root or media_root in priv.parents
            need(not inside_media, cli_gettext("The digital-files folder is outside the public media"), cli_gettext("RAVIX_SHOP_PRIVATE_ROOT (%(priv)s) is inside the public media; digital files would leak") % {"priv": priv})
            advise(priv.is_dir() and os.access(str(priv), os.W_OK), cli_gettext("The digital-files folder is writable"),
                   cli_gettext("The digital-files folder (%(priv)s) has not been created yet or is not writable (it is created on first upload; include it in backups)") % {"priv": priv})
        bad_keys = []
        for item in settings.RAVIX_TRUSTED_PUBLIC_KEYS:
            try:
                signing.normalize_public_key(item.partition(":")[2] or item)
            except signing.SigningError:
                bad_keys.append(item[:20])
        need(not bad_keys, cli_gettext("Trusted keys (env) are valid"), cli_gettext("RAVIX_TRUSTED_PUBLIC_KEYS contains an invalid entry: %(bad_keys)s") % {"bad_keys": bad_keys})
        if settings.RAVIX_PLUGIN_WEB_INSTALL:
            warns.append(cli_gettext("Plugin installation from the web is on (RAVIX_PLUGIN_WEB_INSTALL); keep it on only if you need it, because a plugin is executable code"))
            server(install_dir.is_dir() and os.access(str(install_dir), os.W_OK), cli_gettext("The plugin install folder is writable"),
                 cli_gettext("The plugin install folder (%(install_dir)s) does not exist or is not writable") % {"install_dir": install_dir})
            backup_dir = Path(settings.RAVIX_PLUGIN_BACKUP_DIR)
            server(backup_dir.is_dir() and os.access(str(backup_dir), os.W_OK), cli_gettext("The plugin backup folder is writable"),
                 cli_gettext("The plugin backup folder (%(backup_dir)s) does not exist or is not writable") % {"backup_dir": backup_dir})
            advise(bool(signing.trusted_keys()), cli_gettext("A trusted signing key is defined"), cli_gettext("No trusted key is defined; a signed package is not accepted"))
        advise(not settings.RAVIX_PLUGINS_ALLOW_UNSIGNED, cli_gettext("Unsigned packages are not accepted"), cli_gettext("RAVIX_PLUGINS_ALLOW_UNSIGNED is on; local upload without a signature is allowed"))
        if settings.RAVIX_REGISTRY_SIGNING_KEY_FILE:
            try:
                signing.read_key_file(settings.RAVIX_REGISTRY_SIGNING_KEY_FILE)
                oks.append(cli_gettext("The registry signing key file is readable and has safe permissions"))
            except signing.SigningError as exc:
                errors.append(cli_gettext("Registry signing key: %(exc)s") % {"exc": exc})
        advise(not settings.RAVIX_PLUGINS_SAFE_MODE, cli_gettext("Plugin safe mode is off"), cli_gettext("Plugin safe mode is on (no plugin runs)"))

        for m in oks:
            self.stdout.write(self.style.SUCCESS("  ✔ " + m))
        for m in warns:
            self.stdout.write(self.style.WARNING("  ! " + m))
        for m in errors:
            self.stdout.write(self.style.ERROR("  ✘ " + m))
        self.stdout.write(cli_gettext("\n%(a1)s passed, %(a2)s warnings, %(a3)s errors") % {"a1": len(oks), "a2": len(warns), "a3": len(errors)})
        if errors:
            raise SystemExit(1)
