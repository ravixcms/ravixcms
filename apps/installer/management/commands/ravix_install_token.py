from django.core.management.base import BaseCommand

from apps.installer import services, token


class Command(BaseCommand):
    help = "توکن نصب صفحهٔ /install/ را چاپ می‌کند (اگر نبود می‌سازد). پس از نصب چیزی چاپ نمی‌شود."

    def handle(self, *args, **options):
        if services.is_installed():
            self.stdout.write("نصب قبلاً انجام شده؛ توکنی لازم نیست.")
            return
        if not token.required():
            self.stdout.write("DEBUG روشن است؛ توکن لازم نیست.")
            return
        self.stdout.write(token.expected_token())
