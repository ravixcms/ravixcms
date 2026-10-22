"""ثابت‌های قرارداد قالب (Theme Contract). هر تغییر ناسازگار در این قرارداد باید API_VERSION را افزایش دهد."""

API_VERSION = 1
DEFAULT_THEME = "default"
THEME_MANIFEST = "theme.json"

# قالب‌ها فقط می‌توانند این کتابخانه تگ را بارگذاری کنند ({% load ravin_theme %}).
ALLOWED_LIBRARIES = frozenset({"ravin_theme"})

# قالب‌هایی که هر قالب حتماً باید داشته باشد.
REQUIRED_TEMPLATES = (
    "base.html",
    "home.html",
    "post_list.html",
    "post_detail.html",
    "category_detail.html",
    "tag_detail.html",
    "page_detail.html",
    "search.html",
    "404.html",
)

# تگ‌هایی که base.html باید حتماً استفاده کند (تا سئوی پایه و نوار پیش‌نمایش در همه قالب‌ها تضمین شود).
REQUIRED_BASE_TAGS = ("theme_head", "theme_toolbar")

# فقط فایل‌های غیرقابل‌اجرا روی سرور مجاز هستند. (جاوااسکریپت فقط در مرورگر بازدیدکننده اجرا می‌شود.)
ALLOWED_EXTENSIONS = frozenset(
    {"html", "css", "js", "json", "txt", "md", "svg", "png", "jpg", "jpeg", "gif", "webp", "ico", "woff", "woff2"}
)
ALLOWED_TOP_LEVEL = frozenset({"theme.json", "templates", "static", "README.md", "LICENSE", "LICENSE.txt"})
MAX_MANIFEST_BYTES = 16 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 400

# ------------------------------------------------------------------ بارگذاری ZIP (سقف‌ها در سمت سرور اعمال می‌شوند)
MAX_ZIP_BYTES = 10 * 1024 * 1024  # حجم فایل فشردهٔ بارگذاری‌شده
MAX_EXTRACTED_BYTES = 30 * 1024 * 1024  # مجموع حجم بازشدهٔ همهٔ فایل‌ها
MAX_ZIP_ENTRIES = MAX_FILES * 2  # شامل پوشه‌ها
MAX_COMPRESSION_RATIO = 100  # نسبت حجم بازشده به فشرده؛ بیشتر از آن مشکوک به zip-bomb است
MAX_PATH_LENGTH = 200
MAX_PATH_DEPTH = 8

SCREENSHOT_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp"})

# ------------------------------------------------------------------ سفارشی‌سازی
FONTS = {
    "vazirmatn": {
        "label": "وزیرمتن (بدون‌سریف، پیش‌فرض)",
        "stack": "'Vazirmatn', Tahoma, 'Segoe UI', sans-serif",
        "preload": "fonts/Vazirmatn-Variable.woff2",
    },
    "noto-naskh": {
        "label": "نوتو نسخ (سنتی، مناسب متن‌های طولانی)",
        "stack": "'Noto Naskh Arabic', 'Traditional Arabic', 'Times New Roman', serif",
        "preload": "fonts/noto-naskh-arabic-arabic-wght-normal.woff2",
    },
    "system": {
        "label": "فونت سیستم (سریع‌ترین، بدون دانلود)",
        "stack": "system-ui, -apple-system, 'Segoe UI', Tahoma, sans-serif",
        "preload": None,
    },
}
FONT_SIZES = {"sm": ("کوچک", 16), "md": ("متوسط", 17), "lg": ("بزرگ", 19)}

HOME_SECTION_TYPES = {
    "hero": "معرفی (Hero)",
    "latest_posts": "آخرین نوشته‌ها",
    "category_posts": "نوشته‌های یک دسته‌بندی",
    "categories": "فهرست دسته‌بندی‌ها",
    "page": "نمایش محتوای یک صفحه ثابت",
    "text": "متن آزاد",
}

MENU_LOCATIONS = {"header": "منوی سربرگ", "footer": "منوی پانویس"}
MENU_KINDS = {
    "home": "صفحه اصلی",
    "blog": "فهرست نوشته‌ها",
    "page": "یک صفحه ثابت",
    "category": "یک دسته‌بندی",
    "url": "نشانی دلخواه",
}

MAX_MENU_ITEMS = 12
MAX_SECTIONS = 12
MAX_SOCIAL_LINKS = 6
MIN_CONTRAST = 4.5  # حداقل نسبت کنتراست WCAG AA برای متن عادی
