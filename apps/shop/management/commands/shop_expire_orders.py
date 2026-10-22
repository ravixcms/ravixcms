from django.core.management.base import BaseCommand

from apps.shop import services


class Command(BaseCommand):
    help = "پیش از لغو، از درگاه استعلام می‌گیرد؛ سپس سفارش‌های پرداخت‌نشدهٔ از مهلت گذشته را لغو و موجودی را برمی‌گرداند و تراکنش‌های بی‌نتیجه را می‌بندد. با تایمر هر چند دقیقه اجرا شود."

    def handle(self, *args, **options):
        self.stdout.write(f"{services.expire_pending()} سفارش لغو شد.")
        self.stdout.write(f"{services.expire_stale_payments()} تراکنش بی‌نتیجه بسته شد.")
