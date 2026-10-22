import socket

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "یک ایمیل آزمایشی با تنظیمات فعلی ایمیل می‌فرستد تا SMTP آزموده شود. رمز SMTP هرگز چاپ نمی‌شود."

    def add_arguments(self, parser):
        parser.add_argument("to", help="نشانی ایمیل گیرنده")
        parser.add_argument("--subject", default="ایمیل آزمایشی Ravix")

    def handle(self, *args, to, subject, **options):
        if "@" not in to or " " in to:
            raise CommandError("نشانی ایمیل معتبر نیست.")
        backend = settings.EMAIL_BACKEND
        self.stdout.write(f"backend:  {backend}")
        if backend.endswith("smtp.EmailBackend"):
            self.stdout.write(f"smtp:     {settings.EMAIL_HOST}:{settings.EMAIL_PORT} (TLS={settings.EMAIL_USE_TLS}, کاربر={'تنظیم شده' if settings.EMAIL_HOST_USER else 'خالی'}, رمز={'تنظیم شده' if settings.EMAIL_HOST_PASSWORD else 'خالی'})")
        self.stdout.write(f"from:     {settings.DEFAULT_FROM_EMAIL}")
        body = f"این پیام آزمایشی است و نشان می‌دهد ایمیل Ravix از روی {socket.gethostname()} ارسال می‌شود."
        try:
            sent = EmailMessage(subject, body, to=[to]).send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001 - هر خطای SMTP/شبکه را روشن گزارش بده
            raise CommandError(f"ارسال ناموفق بود: {type(exc).__name__}: {exc}") from None
        if not sent:
            raise CommandError("ارسال ناموفق بود (backend هیچ پیامی نپذیرفت).")
        if backend.endswith("console.EmailBackend"):
            self.stdout.write(self.style.WARNING("backend کنسولی است: پیام در خروجی بالا چاپ شد و ایمیل واقعی نرفت (حالت توسعه)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"ایمیل به backend تحویل داده شد (به {to}). رسیدن به صندوق را خودتان بررسی کنید."))
