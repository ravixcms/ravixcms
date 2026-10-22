import io
import zipfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from apps.core.testing import jpeg_bytes, png_bytes
from apps.media_library.validators import MAX_SIZE, validate_media_file

MB = 1024 * 1024


def f(name, content, ctype="application/octet-stream"):
    return SimpleUploadedFile(name, content, content_type=ctype)


def ooxml(folder):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(f"{folder}document.xml", "<x/>")
    return buf.getvalue()


class AcceptedFilesTests(SimpleTestCase):
    def check(self, name, content, kind):
        self.assertEqual(validate_media_file(f(name, content)), kind, name)

    def test_valid_files_per_type(self):
        self.check("a.png", png_bytes(), "image")
        self.check("a.JPG", jpeg_bytes(), "image")  # پسوند بزرگ
        self.check("a.pdf", b"%PDF-1.7\n%...", "document")
        self.check("a.txt", "متن فارسی".encode(), "document")
        self.check("a.csv", b"a,b\n1,2\n", "document")
        self.check("a.docx", ooxml("word/"), "document")
        self.check("a.xlsx", ooxml("xl/"), "document")
        self.check("a.pptx", ooxml("ppt/"), "document")
        self.check("a.doc", b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"0" * 50, "document")
        self.check("a.mp3", b"ID3" + b"\0" * 20, "audio")
        self.check("a.wav", b"RIFF\0\0\0\0WAVEfmt ", "audio")
        self.check("a.ogg", b"OggS" + b"\0" * 20, "audio")
        self.check("a.mp4", b"\0\0\0\x18ftypmp42" + b"\0" * 20, "video")
        self.check("a.webm", b"\x1a\x45\xdf\xa3" + b"\0" * 20, "video")

    def test_stream_position_reset_after_validation(self):
        upload = f("a.png", png_bytes())
        validate_media_file(upload)
        self.assertEqual(upload.tell(), 0)


class RejectedFilesTests(SimpleTestCase):
    def bad(self, name, content, *, expect=None):
        with self.assertRaises(ValidationError, msg=name) as cm:
            validate_media_file(f(name, content))
        if expect:
            self.assertIn(expect, cm.exception.messages[0], name)

    def test_executable_and_script_extensions(self):
        for name in ("a.exe", "a.php", "a.phtml", "a.py", "a.js", "a.sh", "a.bat", "a.jar", "a.jsp", "a.asp", "a.dll",
                     "a.ps1", "a.vbs", "a.html", "a.htm", "a.xhtml", "a.svg", "a.xml", "a.swf", "a.apk", "a.msi", "a.cgi", ".htaccess"):
            self.bad(name, b"payload", expect="مجاز نیست")

    def test_double_extension_tricks(self):
        for name in ("shell.php.jpg", "shell.php.png", "x.jpg.php", "evil.exe.pdf", "a.html.txt", "pic.svg.png", "x.PHP.JPG", "a.js.png"):
            self.bad(name, png_bytes())

    def test_unknown_or_missing_extension(self):
        self.bad("noext", b"abc")
        self.bad("archive.zip", b"PK\x03\x04")
        self.bad("archive.rar", b"Rar!")
        self.bad("data.json", b"{}")

    def test_executable_content_disguised_with_safe_extension(self):
        self.bad("a.png", b"MZ\x90\x00" + b"\0" * 100, expect="اجرایی")
        self.bad("a.pdf", b"\x7fELF\x02\x01" + b"\0" * 100, expect="اجرایی")
        self.bad("a.txt", b"#!/bin/sh\nrm -rf /\n", expect="اجرایی")
        self.bad("a.mp3", b"\xca\xfe\xba\xbe" + b"\0" * 20)

    def test_content_must_match_extension(self):
        self.bad("a.png", jpeg_bytes(), expect="مطابقت")  # JPEG با پسوند png
        self.bad("a.jpg", png_bytes(), expect="مطابقت")
        self.bad("a.png", b"not an image at all")
        self.bad("a.png", b"<?php system($_GET[0]); ?>")
        self.bad("a.pdf", b"<html>not pdf</html>")
        self.bad("a.docx", b"plain text not a zip")
        self.bad("a.docx", ooxml("xl/"))  # xlsx با پسوند docx
        self.bad("a.doc", b"plain text")
        self.bad("a.mp3", b"nothing")
        self.bad("a.mp4", b"nothing at all here")
        self.bad("a.txt", b"binary\x00data")
        self.bad("a.csv", b"\xff\xfe\xfa invalid utf8 \x80\x81")

    def test_svg_content_with_image_extension_rejected(self):
        self.bad("a.png", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')

    def test_ooxml_with_embedded_executable_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml", "<x/>")
            z.writestr("word/evil.exe", "MZ")
        self.bad("a.docx", buf.getvalue(), expect="اجرایی")

    def test_size_limits_by_kind(self):
        self.bad("a.png", png_bytes() + b"0" * (MAX_SIZE["image"] + 1), expect="حجم")
        self.bad("a.pdf", b"%PDF-" + b"0" * MAX_SIZE["document"], expect="حجم")
        self.bad("a.mp3", b"ID3" + b"0" * MAX_SIZE["audio"], expect="حجم")

    def test_empty_file(self):
        self.bad("a.png", b"", expect="خالی")

    def test_corrupted_image(self):
        self.bad("a.png", png_bytes()[:30])

    def test_decompression_bomb_dimensions_rejected(self):
        # PNG معتبر با ابعاد بسیار بزرگ (فقط هدر): نباید حافظه زیادی مصرف یا پذیرفته شود
        import struct
        import zlib

        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        header = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
        self.bad("big.png", header + chunk(b"IDAT", zlib.compress(b"\0")) + chunk(b"IEND", b""))
