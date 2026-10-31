"""نقاط اتصال «رسمی» که **ناشر** (شما) پیش از ساخت نسخه در همین یک فایل پر می‌کند.

هرچه اینجا باشد پیش‌فرض است: مدیر هر سایت با متغیر محیطی آن را عوض یا خاموش می‌کند (docs/release.md §۳ و docs/registry.md).
مقدارهای خالی یعنی «هنوز راه‌اندازی نشده»: سایت بدون خطا کار می‌کند و فقط بررسی نسخهٔ جدید/مخزن رسمی غیرفعال است.

⚠ فقط کلید **عمومی** اینجا بیاید. کلید خصوصی هرگز در کد، مخزن git، CI یا سرور سایت نمی‌رود.
"""

# وب‌سایت، مستندات و مخزن کد پروژه (فقط برای نمایش پیوند در پنل/نصب‌کننده/User-Agent). متغیرهای محیطی: RAVIX_WEBSITE_URL، RAVIX_DOCS_URL، RAVIX_REPO_URL
OFFICIAL_WEBSITE_URL = "https://ravixcms.com"
OFFICIAL_DOCS_URL = "https://ravixcms.com/docs"
OFFICIAL_REPO_URL = "https://github.com/ravixcms/ravixcms"

# منبع رسمی به‌روزرسانی هسته/قالب: <URL>/updates/v1/index.json ؛ فقط HTTPS.  متغیر محیطی: RAVIX_UPDATE_URL
# تا وقتی هیچ کلید عمومی مورد اعتمادی تنظیم نشده (OFFICIAL_PUBLIC_KEYS خالی و RAVIX_TRUSTED_PUBLIC_KEYS خالی) این منبع **غیرفعال** است:
# بدون کلید هیچ بسته‌ای قابل راستی‌آزمایی نیست. پنل به‌روزرسانی و «سلامت سایت» این را با پیام روشن (نه خطا) نشان می‌دهند.
OFFICIAL_UPDATE_URL = "https://updates.ravixcms.com"

# مخزن رسمی افزونه‌ها و قالب‌ها: <URL>/registry/v1/ ؛ فقط HTTPS.  متغیر محیطی: RAVIX_OFFICIAL_REGISTRY_URL (همان قاعدهٔ کلید)
OFFICIAL_REGISTRY_URL = "https://plugins.ravixcms.com"

# کلیدهای عمومی Ed25519 به‌روزرسانی رسمی هسته/قالب: «برچسب:دامنه:کلید_عمومی_base64».
# دامنه «core» است: این کلیدها فقط بستهٔ به‌روزرسانی هسته/قالب و فهرست آن را تأیید می‌کنند و **هیچ بستهٔ افزونه‌ای** را (دامنهٔ plugin جدا است؛ کلید ناشر افزونه‌ها در RAVIX_TRUSTED_PUBLIC_KEYS یا پنل می‌آید).
# چرخش: کلید جدید را اضافه کنید (قدیمی بماند)، نسخه بدهید، بعد قدیمی را در REVOKED_KEY_IDS بنویسید.
OFFICIAL_PUBLIC_KEYS: tuple[str, ...] = (
    "release-2026:core:V7A0jGCwL7jkdHuM9ndDhScLIAIGBzbb8bFTh3y4BmI=",  # key_id 5e74e93d50dccd92 (اصلی)
    "release-spare-2026:core:idipqBpJCvU6Y6hxiPpyVGborqdrXuYrNimaSeVFhI0=",  # key_id a65cb470630e2ad3 (یدک؛ فقط هنگام چرخش یا ابطال کلید اصلی)
)

# شناسهٔ (key_id، ۱۶ نویسهٔ hex) کلیدهای ابطال‌شده: حتی اگر در OFFICIAL_PUBLIC_KEYS، متغیر محیطی یا پنل باشند پذیرفته نمی‌شوند.
# سایت‌ها همچنین می‌توانند با RAVIX_REVOKED_KEY_IDS محلی ابطال کنند.
REVOKED_KEY_IDS: tuple[str, ...] = ()
