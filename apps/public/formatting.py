"""قالب‌بندی تاریخ شمسی، زمان مطالعه و خلاصه‌سازی متن (توابع خالص)."""
from django.utils.translation import gettext
import html
import re

import jdatetime
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.text import Truncator

from apps.core import localefmt

# Persian month names are used only for the Persian (fa) UI; other languages use Django's locale month names.
JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
_WS = re.compile(r"\s+")


def date_display(value) -> str:
    if not value:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    if not localefmt.is_persian():
        return localefmt.long_date(value)
    j = jdatetime.date.fromgregorian(date=value.date() if hasattr(value, "date") else value)
    return localefmt.digits(f"{j.day} {JALALI_MONTHS[j.month - 1]} {j.year}")


def date_iso(value) -> str:
    return value.isoformat() if value else ""


def plain_text(html_fragment: str) -> str:
    """HTML ← متن ساده (بدون برچسب و با نشانه‌های HTML بازگشایی‌شده)."""
    if not html_fragment:
        return ""
    # برش میان‌برچسب را حذف کن (خروجی Substr دیتابیس ممکن است وسط یک تگ تمام شود)
    last_open, last_close = html_fragment.rfind("<"), html_fragment.rfind(">")
    if last_open > last_close:
        html_fragment = html_fragment[:last_open]
    return _WS.sub(" ", html.unescape(strip_tags(html_fragment))).strip()


def excerpt_from(excerpt: str, html_fragment: str, length: int = 180) -> str:
    text = _WS.sub(" ", excerpt or "").strip() or plain_text(html_fragment)
    return Truncator(text).chars(length)


def reading_time(html_body: str) -> str:
    words = len(plain_text(html_body).split())
    minutes = max(1, round(words / 200))
    return localefmt.digits(gettext("%(minutes)s min read") % {"minutes": minutes})
