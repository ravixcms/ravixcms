# راهنمای توسعه‌دهنده

برای کسی که روی Ravix قالب یا افزونه می‌سازد یا از API آن استفاده می‌کند. (مدیر سایت: [`../admin/`](../admin/README.md).)

| می‌خواهم… | شروع سریع (۱۰ دقیقه) | مرجع کامل |
|---|---|---|
| قالب بسازم | [قالب](theme.md) | [`../themes.md`](../themes.md) |
| افزونه بسازم | [افزونه](plugin.md) | [`../plugins.md`](../plugins.md)، [`../plugin-installer.md`](../plugin-installer.md) |
| از API استفاده کنم | [API](api.md) | [`../api.md`](../api.md)، [`../registry.md`](../registry.md) |
| درگاه پرداخت بنویسم | — | [`../payment-gateways.md`](../payment-gateways.md) |
| بسته منتشر و امضا کنم، سرور به‌روزرسانی/مخزن بالا بیاورم | — | [`../release.md`](../release.md)، [`../registry.md`](../registry.md) |
| در خود Ravix مشارکت کنم | [راه‌اندازی محیط](#محیط-توسعه) | [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md) |

## نقشهٔ کد
```
apps/accounts   کاربر، نقش‌ها و «قابلیت»ها (capability)      apps/content    نوشته، صفحه، دسته، برچسب، ریدایرکت
apps/core       تنظیمات سایت، خطاها، سلامت سایت، ناوبری پنل  apps/public     سایت عمومی (رندر با قالب)
apps/themes     موتور و ثبت قالب                              apps/plugins    سامانهٔ افزونه (کشف، نصب، اثرانگشت، hook)
apps/pluginhub  مخزن افزونه‌ها (API عمومی + انتشار)           apps/updater    به‌روزرسانی امضاشده با پشتیبان و بازگشت
apps/installer  نصب‌کنندهٔ وب                                 apps/builder    صفحه‌ساز
apps/shop       فروشگاه (اختیاری)                             apps/comments   دیدگاه‌ها
themes/         قالب‌ها    plugins/  افزونه‌ها    deploy/ docker/ install.sh  استقرار
```

## سه اصل که همه‌جا صدق می‌کند
۱. **دسترسی بر پایهٔ قابلیت (capability)** است، نه نام نقش: `user.has_cap("content.publish")`، و در ویوها `CapabilityRequiredMixin` با `required_capability`. قابلیت جدید در `apps/accounts/permissions.py` تعریف و به نقش‌ها داده می‌شود.
۲. **قالب کد اجرا نمی‌کند و افزونه فقط با تأیید مدیر اجرا می‌شود.** قالب فقط داده‌های ساده (DTO) می‌بیند؛ افزونه فقط از طریق `PluginAPI` با هسته حرف می‌زند.
۳. **خطای بیرونی نباید سایت را بیندازد.** هر فراخوانی شبکه/افزونه با `try/except` و پیام فارسی؛ سایت عمومی هرگز شبکهٔ بیرونی را صدا نمی‌زند.

## محیط توسعه
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver           # http://127.0.0.1:8000/install/ (DEBUG: بدون توکن)
.venv/bin/python manage.py test                # SQLite؛ سریال
DATABASE_URL=postgres://… .venv/bin/python manage.py test   # PostgreSQL
.venv/bin/python manage.py makemigrations --check --dry-run  # CI همین را می‌سنجد
scripts/bandit_check.sh                        # فقط یافتهٔ تازه شکست می‌دهد
```
CI و فرایند انتشار: [`../release.md`](../release.md). پیش از هر PR: آزمون بنویسید، `CHANGELOG.md` را در بخش `[Unreleased]` به‌روز کنید و مهاجرت جاافتاده نگذارید.
