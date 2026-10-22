"""
سیستم نقش‌ها و دسترسی‌ها.

هر نقش مجموعه‌ای از «قابلیت‌ها» (capability) دارد. ماژول‌های آینده فقط کافی است
قابلیت‌های جدید را اینجا اضافه کنند و با ``user.has_cap("...")`` یا
``CapabilityRequiredMixin`` از آن‌ها استفاده کنند.
"""
from django.db import models


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", "مدیرکل"
    ADMIN = "admin", "مدیر"
    AUTHOR = "author", "نویسنده"
    USER = "user", "کاربر عادی"


ROLE_LEVEL = {
    Role.USER: 10,
    Role.AUTHOR: 20,
    Role.ADMIN: 30,
    Role.SUPER_ADMIN: 40,
}

CAPABILITIES = {
    "dashboard.view": "مشاهده داشبورد",
    "dashboard.stats": "مشاهده آمار کل سایت",
    "profile.manage": "مدیریت پروفایل شخصی",
    "users.view": "مشاهده فهرست کاربران",
    "users.manage": "مدیریت نقش و وضعیت کاربران",
    "settings.manage": "مدیریت تنظیمات سایت",
    "health.view": "مشاهدهٔ «سلامت سایت» و ارسال ایمیل آزمایشی",
    # قابلیت‌های آماده برای مرحله‌های بعد (ماژول محتوا)
    "content.write": "نوشتن محتوا",
    "content.publish": "انتشار و مدیریت نوشته‌های همه",
    "pages.manage": "مدیریت صفحات ثابت",
    "taxonomy.manage": "مدیریت دسته‌بندی‌ها و برچسب‌ها",
    "redirects.manage": "مدیریت ریدایرکت‌ها (انتقال نشانی‌های قدیمی)",
    "comments.moderate": "مدیریت و تأیید دیدگاه‌ها",
    "media.upload": "بارگذاری و مشاهده کتابخانه رسانه",
    "media.manage": "ویرایش و حذف رسانه‌های همه",
    "audit.view": "مشاهده گزارش رویدادهای مدیریتی",
    "themes.manage": "مدیریت قالب و ظاهر سایت",
    "themes.install": "بارگذاری و نصب قالب از فایل ZIP (فقط مدیرکل)",
    "builder.edit": "طراحی صفحات با صفحه‌ساز تصویری",
    "builder.publish": "تأیید و انتشار طرح صفحه‌ساز",
    "plugins.manage": "مدیریت افزونه‌ها (نصب، فعال‌سازی، تنظیمات)",
    "plugins.trust": "مدیریت کلیدهای مورد اعتماد امضای افزونه (ریشهٔ اعتماد؛ فقط مدیرکل)",
    "registry.manage": "مدیریت مخزن افزونه‌ها: ناشران، دسته‌ها، حذف از فهرست (فقط مدیرکل)",
    "sites.manage": "مدیریت سایت‌ها، دامنه‌ها و اعضا (فقط مدیرکل)",
    "shop.manage": "مدیریت محصولات فروشگاه",
    "shop.orders": "مشاهده و پردازش سفارش‌های فروشگاه",
    "shop.gateways": "مدیریت درگاه‌های پرداخت فروشگاه (فعال/غیرفعال و کلیدهای اتصال)",
    "updates.view": "مشاهدهٔ نسخه‌ها، به‌روزرسانی‌های موجود و تاریخچهٔ ارتقا",
    "updates.manage": "اعمال/بازگردانی به‌روزرسانی هسته و قالب‌ها (فقط مدیرکل؛ کد برنامه را جایگزین می‌کند)",
}

_BASIC = {"dashboard.view", "profile.manage"}
_ADMIN = _BASIC | {
    "dashboard.stats",
    "users.view",
    "users.manage",
    "settings.manage",
    "health.view",
    "content.write",
    "content.publish",
    "pages.manage",
    "taxonomy.manage",
    "redirects.manage",
    "comments.moderate",
    "media.upload",
    "media.manage",
    "audit.view",
    "themes.manage",
    "builder.edit",
    "builder.publish",
    "plugins.manage",
    "shop.manage",
    "shop.orders",
    "shop.gateways",
    "updates.view",
}

ROLE_CAPABILITIES = {
    Role.USER: frozenset(_BASIC),
    Role.AUTHOR: frozenset(_BASIC | {"content.write", "media.upload", "builder.edit"}),
    Role.ADMIN: frozenset(_ADMIN),
    Role.SUPER_ADMIN: frozenset(CAPABILITIES),
}
