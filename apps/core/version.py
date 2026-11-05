"""نسخهٔ Ravix و نسخهٔ قرارداد افزونه‌ها. افزونه‌ها با این دو مقدار سازگاری‌شان را اعلام و بررسی می‌کنند."""

RAVIX_VERSION = "0.10.0"
# نام قدیمی؛ در 2.0 حذف می‌شود. به‌روزرسان‌های نسخه‌های قدیمی نسخهٔ یک بسته را با regex روی همین خط می‌خوانند، پس باید
# همیشه عدداً برابر RAVIX_VERSION باشد (آزمون apps/core/tests/test_rename_compat.py این را تضمین می‌کند).
RAVIN_VERSION = "0.10.0"
PLUGIN_API_VERSION = 1  # با تغییر ناسازگار در قرارداد افزونه‌ها (manifest، hookها، PluginAPI) افزایش می‌یابد
THEME_API_VERSION = 1  # با تغییر ناسازگار در قرارداد قالب‌ها (theme.json، تگ‌ها) افزایش می‌یابد
UPDATE_SCHEMA_VERSION = 1  # قالب فهرست به‌روزرسانی (index.json) و بستهٔ هسته (release.json)

# سیاست نسخه‌بندی (docs/upgrade.md):
# - هسته: MAJOR.MINOR.PATCH (SemVer). MAJOR: تغییر ناسازگار؛ MINOR: قابلیت جدید سازگار؛ PATCH: رفع اشکال.
# - افزونه/قالب: MAJOR.MINOR.PATCH در plugin.json / theme.json؛ سازگاری با هسته با بازهٔ «ravix» اعلام می‌شود.
# - دیتابیس: نسخهٔ شمای هر اپ = آخرین مهاجرت اعمال‌شدهٔ آن (django_migrations)؛ هر ارتقا نتیجهٔ مهاجرت را ثبت می‌کند.
