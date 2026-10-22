import os

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile

MAX_IMAGE_SIZE = 2 * 1024 * 1024  # ۲ مگابایت
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}
EXTENSIONS_FOR_FORMAT = {"JPEG": {"jpg", "jpeg"}, "PNG": {"png"}, "GIF": {"gif"}, "WEBP": {"webp"}}


def validate_image_file(file):
    """حجم و قالب واقعی تصویر را (نه فقط پسوند) بررسی می‌کند.

    فقط فایل‌های تازه آپلود شده بررسی می‌شوند تا مقادیر ذخیره‌شده قبلی خطا ندهند.
    """
    # فقط فایل‌های تازه آپلود شده (UploadedFile یا FieldFile ذخیره‌نشده) بررسی می‌شوند.
    if not isinstance(file, UploadedFile) and getattr(file, "_committed", True) is not False:
        return
    if file.size > MAX_IMAGE_SIZE:
        raise ValidationError("حجم تصویر نباید بیشتر از ۲ مگابایت باشد.")
    from PIL import Image

    try:
        file.seek(0)
        with Image.open(file) as img:
            img.verify()
            fmt = img.format
    except Exception as exc:
        raise ValidationError("فایل انتخاب‌شده یک تصویر معتبر نیست.") from exc
    finally:
        file.seek(0)
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("فقط تصاویر JPEG، PNG، GIF و WEBP مجاز هستند.")
    # پسوند باید با نوع واقعی تصویر بخواند؛ وگرنه فایل چندریخت با پسوند .html/.php قابل سرو می‌شد.
    ext = os.path.splitext(file.name or "")[1].lstrip(".").lower()
    if ext not in EXTENSIONS_FOR_FORMAT[fmt]:
        raise ValidationError("پسوند فایل با نوع واقعی تصویر مطابقت ندارد.")
