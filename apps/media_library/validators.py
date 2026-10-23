"""
اعتبارسنجی امنیتی فایل‌های بارگذاری‌شده.

سیاست: فقط فهرست سفید پسوندها مجاز است و محتوای واقعی فایل (امضای باینری) باید با پسوند بخواند.
فایل‌های اجرایی/اسکریپتی/HTML/SVG به هیچ شکلی (حتی با پسوند دوگانه) پذیرفته نمی‌شوند.
"""
from django.utils.translation import gettext
import codecs
import os
import zipfile

from django.core.exceptions import ValidationError
from PIL import Image

KB = 1024
MB = 1024 * KB

IMAGE_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WEBP"}
DOCUMENT_EXTS = {"pdf", "txt", "csv", "doc", "docx", "xls", "xlsx", "ppt", "pptx"}
AUDIO_EXTS = {"mp3", "wav", "ogg"}
VIDEO_EXTS = {"mp4", "webm"}

MAX_SIZE = {"image": 5 * MB, "document": 15 * MB, "audio": 25 * MB, "video": 50 * MB}
MAX_IMAGE_PIXELS = 50_000_000
MAX_FILES_PER_UPLOAD = 10

MIME_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    "pdf": "application/pdf", "txt": "text/plain", "csv": "text/csv",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "mp4": "video/mp4", "webm": "video/webm",
}

# پسوندهای خطرناک: اگر هر بخشی از نام فایل (مثلاً shell.php.jpg) یکی از این‌ها باشد رد می‌شود.
BLOCKED_EXTS = {
    "exe", "dll", "bat", "cmd", "com", "msi", "scr", "pif", "sh", "bash", "zsh", "csh", "ksh", "php", "phtml", "php3",
    "php4", "php5", "php7", "phps", "phar", "py", "pyc", "pyw", "pl", "rb", "js", "mjs", "jsx", "ts", "jsp", "jspx",
    "asp", "aspx", "cgi", "jar", "war", "class", "vbs", "vbe", "ps1", "psm1", "html", "htm", "xhtml", "shtml", "svg",
    "xml", "swf", "hta", "lnk", "apk", "app", "dmg", "iso", "so", "bin", "elf", "wsf", "reg", "htaccess", "inf", "gadget",
}

OLE_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
OOXML_DIR = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
EXECUTABLE_PREFIXES = (b"MZ", b"\x7fELF", b"#!", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa", b"\xcf\xfa\xed\xfe")


def get_extension(name: str) -> str:
    return os.path.splitext(name)[1].lstrip(".").lower()


def kind_for_extension(ext: str):
    if ext in IMAGE_FORMATS:
        return "image"
    if ext in DOCUMENT_EXTS:
        return "document"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _read_head(file, size=65536) -> bytes:
    file.seek(0)
    head = file.read(size)
    file.seek(0)
    return head


def _bad(msg):
    raise ValidationError(msg)


def validate_media_file(file) -> str:
    """فایل را بررسی می‌کند و در صورت معتبر بودن نوع (image/document/audio/video) را برمی‌گرداند."""
    name = os.path.basename(file.name or "")
    if not name or "\x00" in name:
        _bad(gettext("The file name is invalid."))
    parts = [p.lower() for p in name.split(".")[1:]]
    ext = get_extension(name)
    if any(p in BLOCKED_EXTS for p in parts):
        _bad(gettext("Uploading executable files, scripts, HTML and SVG is not allowed."))
    kind = kind_for_extension(ext)
    if kind is None:
        _bad(gettext("File type not allowed. Allowed formats: images (JPG, PNG, GIF, WEBP), documents (PDF, TXT, CSV, DOC/DOCX, XLS/XLSX, PPT/PPTX), audio (MP3, WAV, OGG) and video (MP4, WEBM)."))
    if file.size == 0:
        _bad(gettext("The file is empty."))
    if file.size > MAX_SIZE[kind]:
        _bad((gettext("The file size exceeds the limit (%(value)s MB for this type).") % {"value": (MAX_SIZE[kind] // MB)}))

    head = _read_head(file)
    if head.startswith(EXECUTABLE_PREFIXES):
        _bad(gettext("Executable content was detected in the file and it is not accepted."))

    checker = {"image": _check_image, "document": _check_document, "audio": _check_audio, "video": _check_video}[kind]
    checker(file, ext, head)
    file.seek(0)
    return kind


def _check_image(file, ext, head):
    try:
        file.seek(0)
        with Image.open(file) as img:
            fmt, (w, h) = img.format, img.size
            if w * h > MAX_IMAGE_PIXELS:
                _bad(gettext("The image dimensions are too large."))
            img.verify()
    except ValidationError:
        raise
    except Exception:
        _bad(gettext("The selected file is not a valid image."))
    if fmt != IMAGE_FORMATS[ext]:
        _bad(gettext("The image content does not match the file extension."))


def _check_document(file, ext, head):
    if ext == "pdf":
        if not head.startswith(b"%PDF-"):
            _bad(gettext("The PDF file is not valid."))
    elif ext in OOXML_DIR:
        file.seek(0)
        if not zipfile.is_zipfile(file):
            _bad((gettext("The %(ext)s file is not valid.") % {"ext": ext.upper()}))
        file.seek(0)
        with zipfile.ZipFile(file) as zf:
            names = zf.namelist()
            if "[Content_Types].xml" not in names or not any(n.startswith(OOXML_DIR[ext]) for n in names):
                _bad((gettext("The structure of the %(ext)s file is not valid.") % {"ext": ext.upper()}))
            if sum(i.file_size for i in zf.infolist()) > 300 * MB:
                _bad(gettext("The decompressed content size exceeds the limit."))
            if any(n.lower().endswith((".exe", ".dll", ".bat", ".js", ".vbs")) for n in names):
                _bad(gettext("The file contains executable content."))
    elif ext in {"doc", "xls", "ppt"}:
        if not head.startswith(OLE_MAGIC):
            _bad((gettext("The %(ext)s file is not valid.") % {"ext": ext.upper()}))
    else:  # txt, csv
        if b"\x00" in head:
            _bad(gettext("The text file is not valid."))
        try:
            codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        except UnicodeDecodeError:
            _bad(gettext("The text file must be saved with UTF-8 encoding."))


def _check_audio(file, ext, head):
    ok = {
        "mp3": head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0),
        "wav": head[:4] == b"RIFF" and head[8:12] == b"WAVE",
        "ogg": head.startswith(b"OggS"),
    }[ext]
    if not ok:
        _bad(gettext("The audio file content does not match its extension."))


def _check_video(file, ext, head):
    ok = {"mp4": head[4:8] == b"ftyp", "webm": head.startswith(b"\x1a\x45\xdf\xa3")}[ext]
    if not ok:
        _bad(gettext("The video file content does not match its extension."))
