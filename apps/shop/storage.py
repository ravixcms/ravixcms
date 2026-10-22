"""ذخیره‌گاه خصوصی فایل‌های محصول دیجیتال: خارج از MEDIA_ROOT/STATIC_ROOT و بدون نشانی عمومی؛ فقط از طریق view دانلود (پس از پرداخت)."""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    @property
    def base_location(self):
        return str(settings.SHOP_PRIVATE_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("فایل‌های دیجیتال نشانی عمومی ندارند.")


def private_storage():
    return PrivateStorage()
