# ساخت افزونه — شروع سریع

افزونه **کد پایتون** است و فقط پس از تأیید مدیر و با اثرانگشت تأییدشده اجرا می‌شود. مرجع کامل: [`../plugins.md`](../plugins.md)، نصب و امضا: [`../plugin-installer.md`](../plugin-installer.md).

## ۱. اسکلت
```bash
python manage.py startplugin my_hello --name "سلام" --author "من"     # --models برای جدول دیتابیس
python manage.py check_plugins my_hello                                # manifest و سازگاری؛ کدی اجرا نمی‌شود
```
```
plugins/my_hello/
├── plugin.json            قرارداد اعلامی (کلید ناشناخته = خطا)
├── __init__.py            خالی/بدون اثر جانبی
├── views.py               def hello(request, api): …
└── templates/hello.html   {% extends "plugins/page.html" %} + بلوک plugin_content
```

## ۲. قرارداد `plugin.json` در یک نگاه
```json
{ "slug": "my_hello", "name": "سلام", "version": "1.0.0", "api_version": 1, "ravix": ">=0.6.0,<1.0.0",
  "capabilities": {"my_hello.view": "مشاهده"},
  "settings": [{"key": "greeting", "label": "سلام", "type": "text", "default": "سلام!"}],
  "admin_pages": [{"key": "hello", "title": "سلام", "view": "views:hello", "capability": "my_hello.view", "menu": true, "section": "content"}],
  "public_routes": [{"path": "", "view": "views:form", "methods": ["GET", "POST"]}],
  "hooks": [{"name": "public.seo", "handler": "hooks:apply_seo", "priority": 50}] }
```
- `slug` = نام پوشه (`[a-z][a-z0-9_]{2,30}`)؛ `view`/`handler` به شکل `"ماژول:تابع"` و **نسبت به همان افزونه** import می‌شود.
- مجوزها باید با `"<slug>."` شروع شوند؛ صفحهٔ بی‌مجوز پیش‌فرضش `plugins.manage` است.
- مسیر عمومی زیر `/ext/<slug>/…` می‌آید؛ CSRF و مجوز را هسته اعمال می‌کند (`csrf_exempt` ممکن نیست).
- ویو: `def view(request, api)` و تنها راه تعامل با هسته `PluginAPI` است (`api.setting()`، `api.render_admin()`، …؛ فهرست در مرجع).

## ۳. چرخهٔ اجرا
۱. پوشه در `plugins/` (یا `RAVIX_PLUGIN_INSTALL_DIR`) ← شناسایی: فقط `plugin.json` خوانده می‌شود.
۲. پنل **افزونه‌ها ← بررسی و نصب** (اثرانگشت SHA-256 تأیید می‌شود) ← **فعال‌سازی**.
۳. هر تغییر فایل بعد از تأیید، افزونه را متوقف می‌کند تا دوباره تأیید شود (در توسعه: بعد از هر ویرایش دوباره تأیید کنید).
۴. خطای افزونه صفحه را نمی‌اندازد؛ ثبت می‌شود و پس از خطاهای پیاپی غیرفعال می‌شود («گزارش افزونه‌ها»).

## ۴. مدل دیتابیس (اختیاری)
`"models": true` ⇒ اپ با برچسب `plugin_<slug>`؛ جدول‌ها باید `plugin_<slug>_*` باشند و مهاجرت‌ها فقط عملیات ساختاری (نه `RunPython`/`RunSQL`). ساخت مهاجرت (فقط DEBUG): `python manage.py plugin_makemigrations my_plugin`.

## ۵. hookها
فقط فهرست رسمی (`apps/plugins/hooks_registry.py`): `public.seo`، `comments.before_save`، `comments.created`، `comments.status_changed`، `content.saved` (رزرو). خروجی filterها محدود و پاک‌سازی می‌شود (مثلاً `public.seo` فقط `title, description, canonical, robots, og_type, og_image`).

## ۶. آزمون و انتشار
- آزمون افزونه با همان `manage.py test` (نمونه‌ها: `plugins/contact_form`، `plugins/seo_toolkit`).
- **امضا** (کلید خصوصی را هرگز چاپ/commit نکنید):
  ```bash
  python manage.py plugin_keygen --out /secure/publisher.key
  cd plugins && zip -r my_hello.zip my_hello -x '*/__pycache__/*'
  python manage.py plugin_sign my_hello.zip --key-file /secure/publisher.key   # signature + key_id
  ```
- **انتشار در مخزن:** `curl -H "Authorization: Bearer $TOKEN" -F package=@my_hello.zip -F publish=1 https://registry…/registry/v1/publish/` ([`../registry.md`](../registry.md)). سایت‌ها فقط بستهٔ امضاشده با کلید مورد اعتماد خودشان را می‌پذیرند.
