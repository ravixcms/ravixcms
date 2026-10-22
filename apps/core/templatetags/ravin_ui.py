"""نام منسوخ ``{% load ravin_ui %}``؛ از ``{% load ravix_ui %}`` استفاده کنید. تا Ravix 2.0 همان تگ‌ها/فیلترهاست.

افزونه‌های نصب‌شده با نسخهٔ قدیمی هنوز این نام را در قالب‌هایشان دارند (پوشهٔ plugins/ هنگام ارتقای هسته دست نمی‌خورد).
"""
from apps.core.deprecation import legacy_library
from apps.core.templatetags.ravix_ui import register as _new

register = legacy_library(_new, "ravin_ui", "ravix_ui")
