import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.installer import services


class Command(BaseCommand):
    help = ("نصب بدون وب (برای اسکریپت‌ها/Docker): مهاجرت‌ها را اجرا و مدیرکل و تنظیمات اولیه را می‌سازد. "
            "رمز عبور فقط از متغیر محیطی (--password-env) یا پرسش تعاملی؛ هرگز از آرگومان خط فرمان.")

    def add_arguments(self, parser):
        parser.add_argument("--site-name", default="راویکس")
        parser.add_argument("--language", default="fa")
        parser.add_argument("--timezone", default="Asia/Tehran")
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--password-env", metavar="VAR", help="نام متغیر محیطی که رمز عبور را دارد")
        parser.add_argument("--skip-migrate", action="store_true")

    def handle(self, *args, **o):
        if services.is_installed():
            raise CommandError("نصب قبلاً انجام شده است.")
        password = os.environ.get(o["password_env"]) if o["password_env"] else None
        if not password:
            if o["password_env"]:
                raise CommandError(f"متغیر {o['password_env']} تنظیم نشده یا خالی است.")
            password = getpass.getpass("رمز عبور مدیرکل: ")
            if password != getpass.getpass("تکرار رمز عبور: "):
                raise CommandError("دو رمز عبور یکسان نیستند.")
        try:
            validate_password(password, user=User(username=o["username"], email=o["email"]))
        except ValidationError as exc:
            raise CommandError("؛ ".join(exc.messages)) from exc
        if not o["skip_migrate"]:
            services.run_migrations()
        try:
            services.perform_install(site_name=o["site_name"], language=o["language"], timezone_name=o["timezone"],
                                     username=o["username"], email=o["email"].strip().lower(), password=password)
        except (services.InstallError, services.AlreadyInstalled) as exc:
            raise CommandError(str(exc) or "نصب قبلاً انجام شده است.") from exc
        self.stdout.write(self.style.SUCCESS("نصب کامل شد."))
