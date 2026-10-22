"""نام منسوخ کتابخانهٔ تگ؛ از ``{% load ravix_theme %}`` استفاده کنید. تا Ravix 2.0 همان تگ‌هاست.

عمداً بیرون از ``templatetags/`` است تا Django آن را برای موتور اصلی بارگذاری نکند. هشدار هم هنگام اعتبارسنجی قالب
(``apps/themes/manifest.py``) و هم هنگام اولین استفاده در رندر داده می‌شود.
"""
from apps.core.deprecation import legacy_library
from apps.themes.templatetags.ravix_theme import register as _new

register = legacy_library(_new, "ravin_theme", "ravix_theme")
