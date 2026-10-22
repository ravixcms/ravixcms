# ساخت قالب — شروع سریع

قالب فقط HTML + CSS + JS + تصویر است؛ **هیچ کد پایتونی** در آن اجرا نمی‌شود. مرجع کامل (همهٔ متغیرها، DTOها، CSP): [`../themes.md`](../themes.md).

## ۱. اسکلت
```bash
python manage.py startheme my-theme --name "قالب من" --author "نام شما"
python manage.py check_themes my-theme     # اعتبارسنجی با دلیل خطا (بدون نامک: همه)
```
پوشهٔ `themes/my-theme/` از روی `default` ساخته می‌شود (برای مسیر بیرون پروژه: `RAVIX_EXTRA_THEME_DIRS`).

```
themes/my-theme/
├── theme.json        { "api_version": 1, "slug": "my-theme", "name": …, "version": "0.1.0", "author": … }
├── templates/        base.html home.html post_list.html post_detail.html category_detail.html
│                     tag_detail.html page_detail.html search.html 404.html  (همه الزامی) + partials دلخواه
└── static/           css/ js/ تصویر/ فونت  (فایل .html ممنوع)
```

## ۲. قوانین قالب (چرا چیزی «کار نمی‌کند»)
- فقط `{% load ravix_theme %}` مجاز است؛ در `<head>` از `{% theme_head %}` و برای فایل‌های خودتان از `{% theme_static 'css/theme.css' %}` استفاده کنید (الگو: `themes/default/templates/base.html`). `request`، `user`، `settings` و ORM در دسترس نیست.
- فقط متغیرهای اعلام‌شده برای هر صفحه (جدول در مرجع)؛ مثلاً `post_detail.html` فقط `post`، `related`، `comments` و عمومی‌های `site/theme/header/footer/menus` را می‌بیند.
- اسکریپت درون‌خط ممنوع (CSP `script-src 'self'`): جاوااسکریپت باید فایل جدا در `static/` باشد.
- رنگ‌ها را از متغیرهای `--rv-*` بگیرید (`--rv-primary`، `--rv-bg`، `--rv-text`…) تا «سفارشی‌سازی ظاهر» مدیر روی قالب شما کار کند.
- هر بار CSS/JS را تغییر می‌دهید `version` را در `theme.json` بالا ببرید (کش مرورگر).

## ۳. توسعه و آزمایش
۱. پوشه را بسازید؛ در پنل **ظاهر ← قالب‌ها** قالب شما در فهرست است (نامعتبر = با دلیل).
۲. **پیش‌نمایش** بزنید (روی سایت زنده اثری ندارد)، ویرایش کنید، رفرش کنید.
۳. در حالت تولید فایل‌های ایستا باید جمع شوند: `python manage.py collectstatic --noinput`.
۴. خطای رایج: قالب الزامی نبود، فایل با پسوند غیرمجاز، `{% load %}` غیرمجاز، `slug` با نام پوشه یکی نیست.
۵. سازگاری با بخش‌های صفحهٔ اصلی: `supports.home_sections` در `theme.json`.

## ۴. توزیع
- **کپی پوشه** روی سرور (امن‌ترین).
- **ZIP از پنل** فقط مدیرکل و فقط با تنظیم آگاهانهٔ سرور: بسته پیش از نصب کامل سنجیده می‌شود و خودکار فعال نمی‌شود.
- **به‌روزرسانی امضاشده:** `python manage.py ravix_release theme --theme-dir themes/my-theme --out dist/ --key-file …` (کلید آفلاین؛ [`../release.md`](../release.md)).
