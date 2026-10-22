"""ذخیره‌گاه خصوصی بسته‌ها: خارج از MEDIA_ROOT و STATIC_ROOT؛ فقط از طریق view دانلود (پس از بازبینی SHA-256) سرو می‌شود."""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PackageStorage(FileSystemStorage):
    @property
    def base_location(self):
        return str(settings.RAVIX_REGISTRY_STORAGE)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):  # هرگز نشانی مستقیم عمومی ندارد
        raise ValueError("بسته‌ها نشانی عمومی ندارند.")


def package_storage():
    return PackageStorage()
