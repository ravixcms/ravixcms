"""نسخهٔ Ravin CMS و نسخهٔ قرارداد افزونه‌ها. افزونه‌ها با این دو مقدار سازگاری‌شان را اعلام و بررسی می‌کنند."""

RAVIN_VERSION = "0.6.1"
PLUGIN_API_VERSION = 1  # با تغییر ناسازگار در قرارداد افزونه‌ها (manifest، hookها، PluginAPI) افزایش می‌یابد
THEME_API_VERSION = 1  # با تغییر ناسازگار در قرارداد قالب‌ها (theme.json، تگ‌ها) افزایش می‌یابد
UPDATE_SCHEMA_VERSION = 1  # قالب فهرست به‌روزرسانی (index.json) و بستهٔ هسته (release.json)

# سیاست نسخه‌بندی (docs/upgrade.md):
# - هسته: MAJOR.MINOR.PATCH (SemVer). MAJOR: تغییر ناسازگار؛ MINOR: قابلیت جدید سازگار؛ PATCH: رفع اشکال.
# - افزونه/قالب: MAJOR.MINOR.PATCH در plugin.json / theme.json؛ سازگاری با هسته با بازهٔ «ravin» اعلام می‌شود.
# - دیتابیس: نسخهٔ شمای هر اپ = آخرین مهاجرت اعمال‌شدهٔ آن (django_migrations)؛ هر ارتقا نتیجهٔ مهاجرت را ثبت می‌کند.
