"""منطق نصب؛ مشترک بین صفحهٔ وب، دستور ``ravix_install`` و اسکریپت‌ها."""
from django.utils.translation import gettext
import logging

from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import SiteSettings
from apps.core.regional import language_choices, valid_timezone

from . import token

logger = logging.getLogger("ravix.installer")

_installed = False  # پس از true شدن هرگز برنمی‌گردد (ارزان‌کردن گیت در هر درخواست)


class AlreadyInstalled(Exception):
    pass


class InstallError(Exception):
    """خطای قابل‌نمایش به مدیر (پیام فارسی)."""


def reset_cache() -> None:  # برای آزمون‌ها
    global _installed
    _installed = False


def is_installed() -> bool:
    """نصب‌شده = ``installed_at`` ثبت شده، یا (سایت قدیمی) دست‌کم یک کاربر وجود دارد؛ در این حالت ثبت می‌شود.

    هر خطای دیتابیس (جدول‌ها هنوز ساخته نشده‌اند) یعنی «نصب نشده». هرگز بر اساس خطا true نمی‌شود.
    """
    global _installed
    if _installed:
        return True
    try:
        row = SiteSettings.objects.filter(pk=1).values_list("installed_at", flat=True).first()
        if row is not None:
            _installed = True
            return True
        if User.objects.exists():
            SiteSettings.load()
            SiteSettings.objects.filter(pk=1, installed_at__isnull=True).update(installed_at=timezone.now())
            _installed = True
            return True
    except DatabaseError:
        return False
    return False


def pending_migrations() -> list[str]:
    try:
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except DatabaseError:
        return [gettext("(database)")]
    return [f"{m.app_label}.{m.name}" for m, _ in plan]


def database_error() -> str:
    """رشتهٔ خالی اگر دیتابیس در دسترس است؛ وگرنه نوع خطا (بدون جزئیات اتصال/رمز)."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return ""
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def run_migrations() -> None:
    call_command("migrate", interactive=False, verbosity=0)


def validate_regional(language: str, tz: str) -> None:
    if language not in dict(language_choices()):
        raise InstallError(gettext("The selected language is not supported."))
    if not valid_timezone(tz):
        raise InstallError(gettext("The time zone is invalid."))


def default_site_name(language: str) -> str:
    """The suggested site name ("Ravix") written in ``language``."""
    from django.utils import translation

    with translation.override(language):
        return gettext("Ravix")


def _all_default_names() -> set:
    from apps.core.regional import language_choices

    return {default_site_name(code) for code, _ in language_choices()}


def perform_install(*, site_name: str, language: str, timezone_name: str, username: str, email: str, password: str) -> User:
    """ساخت مدیرکل و ثبت تنظیمات اولیه در یک تراکنش؛ نصب دوم (حتی هم‌زمان) AlreadyInstalled می‌دهد."""
    global _installed
    validate_regional(language, timezone_name)
    if site_name.strip() in _all_default_names():  # the suggested name, written in another language than the chosen one
        site_name = default_site_name(language)
    with transaction.atomic():
        SiteSettings.load()
        site = SiteSettings.objects.select_for_update().get(pk=1)
        if site.installed_at is not None or User.objects.exists():
            raise AlreadyInstalled()
        user = User.objects.create_superuser(username=username, email=email, password=password)
        site.site_name = site_name
        site.language = language
        site.timezone = timezone_name
        site.admin_email = email
        site.installed_at = timezone.now()
        site.save()
    _installed = True
    try:  # محتوای نمونه اختیاری است؛ خطا نباید نصب را خراب کند
        from apps.content.samples import create_samples

        create_samples(author=user)
    except Exception:  # noqa: BLE001
        logger.exception("ساخت محتوای نمونه ناموفق بود")
    token.discard_token()
    logger.info("نصب Ravix کامل شد؛ مدیرکل: %s", username)
    return user
