"""بهینه‌سازی تصویر آپلودشده: کوچک‌کردن ابعاد، حذف EXIF/متادیتا (حریم خصوصی) و فشرده‌سازی. فقط فایل‌های تازه را پردازش می‌کند."""
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_SIDE = 1600


def optimize_image_field(field_file, *, max_side: int = MAX_SIDE):
    """``field_file`` (FieldFile ذخیره‌نشده) را در جا با نسخهٔ بهینه جایگزین می‌کند؛ در هر خطا فایل اصلی دست‌نخورده می‌ماند."""
    if not field_file or getattr(field_file, "_committed", True):
        return
    try:
        field_file.file.seek(0)
        with Image.open(field_file.file) as img:
            fmt = img.format
            if fmt == "GIF":  # انیمیشن را خراب نمی‌کنیم
                return
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_side, max_side), Image.LANCZOS)
            out = BytesIO()
            if fmt == "PNG":
                img.save(out, "PNG", optimize=True)
            elif fmt == "WEBP":
                img.save(out, "WEBP", quality=82, method=4)
            else:
                img = img.convert("RGB")
                img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        name = field_file.name
        field_file.save(name, ContentFile(out.getvalue()), save=False)
    except Exception:  # noqa: BLE001
        try:
            field_file.file.seek(0)
        except Exception:  # noqa: BLE001
            pass
