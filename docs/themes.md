# راهنمای قالب‌ها در Ravin CMS

این سند دو بخش دارد: **افزودن قالب جدید** (گام‌به‌گام) و **قرارداد قالب** (مرجع کامل).
برای مدیر سایت، همهٔ مدیریت قالب از پنل است: «ظاهر سایت ← قالب‌ها / سفارشی‌سازی ظاهر».

---

## ۱. افزودن یک قالب جدید (گام‌به‌گام)

### گام ۱ – ساخت پوشهٔ قالب از روی قالب پیش‌فرض

```bash
python manage.py startheme my-theme --name "قالب من" --author "نام شما"
```

پوشهٔ `themes/my-theme/` از روی قالب `default` ساخته می‌شود (نسخهٔ `0.1.0`). نامک فقط شامل حروف کوچک
انگلیسی، عدد، `-` و `_` است. اگر دستور را نمی‌خواهید، پوشهٔ `themes/default` را دستی کپی کنید و
مقدار `slug` را در `theme.json` با نام پوشه یکی کنید.

برای نصب قالب‌ها در مسیری خارج از پروژه، آن را در `RAVIN_EXTRA_THEME_DIRS` (جداشده با ویرگول) بگذارید.

### گام ۲ – ویرایش `theme.json`

```json
{
  "api_version": 1,
  "slug": "my-theme",
  "name": "قالب من",
  "version": "0.1.0",
  "author": "نام شما",
  "description": "توضیح کوتاه (اختیاری)",
  "screenshot": "screenshot.png",
  "supports": { "home_sections": ["hero", "latest_posts", "categories", "text"] }
}
```

| فیلد | الزامی | توضیح |
|---|---|---|
| `api_version` | بله | باید `1` باشد (نسخهٔ این قرارداد). |
| `slug` | بله | دقیقاً هم‌نام پوشه. |
| `name`، `version`، `author` | بله | `version` به شکل `1.2.3`. هر بار CSS/JS را تغییر دادید، نسخه را بالا ببرید تا کش مرورگرها نو شود. |
| `description`، `screenshot` | خیر | تصویر باید داخل `static/` و png/jpg/webp باشد (پیشنهاد: ۶۴۰×۴۰۰). |
| `supports.home_sections` | خیر | نوع بخش‌های صفحهٔ اصلی که قالب نمایش می‌دهد (پیش‌فرض: همه). |

### گام ۳ – ظاهر را بسازید

قالب‌های الزامی در `templates/` (نبودن هر کدام قالب را نامعتبر می‌کند):

| فایل | صفحه | متغیرهای اصلی |
|---|---|---|
| `base.html` | چارچوب مشترک | `site`، `theme`، `header`، `footer`، `menus` |
| `home.html` | صفحهٔ اصلی | `sections` |
| `post_list.html` | فهرست نوشته‌ها | `posts`، `pagination`، `categories` |
| `post_detail.html` | یک نوشته | `post`، `related`، `comments` |
| `category_detail.html` | یک دسته‌بندی | `category`، `children`، `posts`، `pagination` |
| `tag_detail.html` | یک برچسب | `tag`، `posts`، `pagination` |
| `page_detail.html` | صفحهٔ ثابت | `page` |
| `search.html` | جستجو | `query`، `searched`، `posts`، `pages`، `pagination`، `total` |
| `404.html` | صفحهٔ پیدا نشد | — |

همهٔ صفحه‌ها `breadcrumbs` (مسیر صفحه) هم دارند. قالب‌ها فقط با `{% extends "base.html" %}` و
`{% include "partials/..." %}` به فایل‌های **همان قالب** دسترسی دارند.

### دیدگاه‌ها در `post_detail.html`

متغیر `comments` (اختیاری برای قالب؛ قالبی که آن را نمایش ندهد دیدگاهی نشان نمی‌دهد) فیلدهای `count`، `items` (درخت `CommentItem` با `author_name`، `date_display`، `date_iso`، `body_html` (پاک‌سازی‌شده)، `depth`، `reply_url`، `replies`)، `open`، `action_url`، `honeypot_name`، `notice` (`pending`/`ok`)، `errors` (`name`/`email`/`body`/`form`)، `values`، `reply_to_id`، `reply_to_name`، `cancel_reply_url`، `max_length` را دارد.
فرم باید `method="post"` به `comments.action_url` باشد و فیلد honeypot (`name="{{ comments.honeypot_name }}"`) را با CSS پنهان داشته باشد؛ توکن CSRF لازم نیست (صفحه‌های عمومی کش می‌شوند). نمونه: `themes/default/templates/partials/comments.html`. برای تودرتویی از یک partial بازگشتی استفاده کنید (`partials/comment.html`).
نام متغیرها نباید با «_» شروع شود (موتور قالب آن را رد می‌کند).

`base.html` **باید** این دو تگ را داشته باشد (برای سئوی پایه و نوار پیش‌نمایش):

```django
{% load ravin_theme %}
<head>{% theme_head %} <link rel="stylesheet" href="{% theme_static 'css/theme.css' %}"> </head>
<body>{% theme_toolbar %} ...
```

- `{% theme_head %}`: charset، viewport، عنوان، توضیحات، canonical، پیوندهای autodiscovery فید RSS/Atom (`<link rel="alternate">`، از `feeds`)، Open Graph، JSON-LD، favicon، پیش‌بارگذاری فونت
  و متغیرهای رنگ/فونت. باید اولین مورد داخل `<head>` باشد.
- `{% theme_static 'مسیر' %}`: نشانی فایل داخل `static/` همین قالب (با `?v=<version>`).
- `{% theme_toolbar %}`: نوار مدیر و هشدار حالت پیش‌نمایش؛ بلافاصله پس از `<body>`.
- فیلتر `|fa_digits`: تبدیل اعداد به ارقام فارسی.

**رنگ‌ها و فونت** را مدیر از پنل تعیین می‌کند. CSS قالب فقط از این متغیرها استفاده کند و مقدارهای پیش‌فرض را با
`:where(:root){...}` بدهد تا مقدار مدیر را نپوشاند:

`--rv-primary`، `--rv-on-primary`، `--rv-primary-soft`، `--rv-bg`، `--rv-surface`، `--rv-border`، `--rv-text`،
`--rv-muted`، `--rv-font`، `--rv-font-size`

### گام ۴ – بررسی

```bash
python manage.py check_themes my-theme
```

خطاها به فارسی گزارش می‌شوند (ساختار، فایل‌های ممنوع، تگ‌های غیرمجاز، خطای نحوی قالب).

### گام ۵ – فعال‌سازی

در پنل: «قالب‌ها» ← **پیش‌نمایش** (فقط برای شما، بدون اثر روی بازدیدکنندگان) ← **فعال‌سازی**.
قالب فعال در دیتابیس (`ThemeConfig.active_theme`) ذخیره می‌شود. «غیرفعال‌سازی» قالب فعال، قالب پیش‌فرض را برمی‌گرداند.
فهرست قالب‌ها پس از افزودن پوشهٔ جدید بدون راه‌اندازی مجدد دیده می‌شود. برای فایل‌های استاتیک (CSS/JS/تصویر) در production باید
`python manage.py collectstatic --noinput` اجرا شود؛ اینکه راه‌اندازی مجدد هم لازم است یا نه، به نحوهٔ سرو استاتیک بستگی دارد
(نتیجهٔ آزمایش واقعی در بخش «فایل‌های استاتیک پس از نصب» زیر آمده است).

---

## ۲. قرارداد قالب (مرجع)

### ساختار

```
themes/<slug>/
├── theme.json          # الزامی
├── templates/          # فقط فایل‌های .html (قالب‌های الزامی بالا + partials دلخواه)
├── static/             # css, js, تصویر، فونت (فایل .html ممنوع)
├── README.md           # اختیاری
└── LICENSE             # اختیاری
```

هر چیز دیگری در ریشه، قالب را نامعتبر می‌کند. قالب نامعتبر در فهرست با دلیل نشان داده می‌شود، قابل فعال‌سازی نیست و
اگر قالب فعالِ سایت بعداً خراب یا حذف شود، سایت خودکار با قالب پیش‌فرض نمایش داده می‌شود (انتخاب مدیر در دیتابیس
دست‌نخورده می‌ماند).

### امنیت: چه چیزی ممنوع است و چرا

| محدودیت | دلیل |
|---|---|
| هیچ کد پایتون از پوشهٔ قالب import یا اجرا نمی‌شود؛ نصب قالب کپی پوشه است یا (فقط با تنظیم آگاهانهٔ سرور و فقط مدیرکل) بارگذاری ZIP که قبل از نصب به‌طور کامل سنجیده می‌شود (بخش ۳) | جلوگیری از اجرای کد دلخواه روی سرور |
| فقط پسوندهای غیرقابل‌اجرا: `html css js json txt md svg png jpg jpeg gif webp ico woff woff2` | `.py`، `.sh`، `.php` و … رد می‌شوند |
| `html` فقط در `templates/` و هرگز در `static/` | فایل‌های static مستقیم از وب سرو می‌شوند |
| symlink، حجم بیش از ۵MB برای هر فایل، بیش از ۴۰۰ فایل | جلوگیری از خروج از پوشه و سوءاستفاده از منابع |
| موتور قالب جدا و قفل‌شده: فقط پوشهٔ `templates/` همان قالب، فقط `{% load ravin_theme %}`، بدون `{% debug %}`، بدون context processor | قالب به `request`، `user`، `settings`، ORM و قالب‌های پنل دسترسی ندارد |
| قالب فقط داده‌های ساده‌ی اعلام‌شده (DTO در `apps/public/dto.py`) را می‌بیند | نشت نکردن فیلدهای حساس |
| CSP: `script-src 'self'`، بدون اسکریپت درون‌خط | جلوگیری از XSS؛ جاوااسکریپت قالب باید فایل جدا در `static/` باشد |

متن نوشته‌ها هنگام ذخیره با `nh3` پاک‌سازی می‌شود، پس `{{ post.body_html }}` امن است. بقیهٔ مقادیر
خودکار escape می‌شوند.

### داده‌هایی که قالب می‌بیند

- `site`: `name`، `tagline`، `description`، `logo_url`، `favicon_url`، `email`، `phone`، `address`، `home_url`، `search_url`
- `theme`: `slug`، `name`، `version`؛ `theme_vars`: مقدارهای رنگ/فونت (برای `theme_head`)
- `header`: `show_tagline`، `sticky`، `show_search`، `cta` (`label`/`url` یا خالی)
- `footer`: `text`، `show_menu`، `show_contact`، `social` (فهرست `Link`)
- `feeds`: فهرست `FeedLink` با `title`، `url` (مطلق)، `type` (`application/rss+xml` یا `application/atom+xml`)؛ `theme_head` خودش آن‌ها را در `<head>` می‌گذارد، نیازی به کار در قالب نیست. در صفحهٔ دسته/برچسب، فید همان دسته/برچسب اول می‌آید و بعد فید کل سایت.
- `menus.header` / `menus.footer`: فهرست `Link` با `label`، `url`، `active`، `new_tab` (آیتم‌های بدون مقصد قابل‌نمایش حذف شده‌اند)
- `viewer`: برای مدیر واردشده (`name`، `panel_url`، `edit_url`، `customize_url`) وگرنه خالی
- `post`/`posts[]`: `title`، `url`، `excerpt`، `image_url`، `author_name`، `date_display`، `date_iso`، `categories`، `tags`،
  `reading_time` و `body_html` (فقط در صفحهٔ خود نوشته)
- `page`: `title`، `url`، `body_html`، `date_display`، `date_iso`، و دو فیلد **اختیاری** مرحلهٔ چهارم (صفحه‌ساز):
  `builder` (درست یعنی `body_html` خروجی صفحه‌ساز است) و `show_title` (فقط برای صفحات صفحه‌ساز: عنوان بالای محتوا نمایش داده شود؟).
  قالب‌های قدیمی بدون تغییر کار می‌کنند (بدنه همان‌جا که بود نمایش داده می‌شود)؛ برای نمایش **تمام‌عرض** و کنترل عنوان،
  `page_detail.html` را مثل قالب پیش‌فرض (`{% if page.builder %}…{% endif %}`) به‌روز کنید. بدنهٔ صفحه‌ساز خودکفاست:
  HTML امن و یک بلوک `<style>` که فقط به کلاس‌های `bx-*` و متغیرهای `--rv-primary`/`--rv-on-primary` قالب وابسته است. جزئیات: `docs/page-builder.md`.
- `category`/`categories[]`: `name`، `slug`، `url`، `description`، `post_count`؛ `tag`: `name`، `url`
- `pagination`: `page`، `num_pages`، `total`، `has_prev`، `has_next`، `prev_url`، `next_url`، `pages[]` (`number`، `url`، `current`؛ `number=0` یعنی «…»)
- `sections[]` (صفحهٔ اصلی): `type` ∈ `hero | latest_posts | category_posts | categories | page | text`، به‌همراه
  `title`، `subtitle`، `body_html`، `button_label`، `button_url` و بسته به نوع `posts`، `categories` یا `page`

هر فیلد جدید باید در `apps/public/dto.py` تعریف و همین‌جا مستند شود. تغییر ناسازگار در این قرارداد یعنی افزایش `api_version`.

### سفارشی‌سازی توسط مدیر (بدون دست‌زدن به کد)

«سفارشی‌سازی ظاهر» چهار بخش دارد: رنگ‌ها/فونت/لوگو/آیکون، سربرگ و پانویس، منوها، بخش‌های صفحهٔ اصلی. تغییرات
**ابتدا در پیش‌نویس** ذخیره می‌شوند؛ مدیر می‌تواند در پنجرهٔ پیش‌نمایش یا با «پیش‌نمایش در سایت» نتیجه را ببیند و سپس
**منتشر کند** یا **دور بریزد**. فقط مدیرانی که قابلیت `themes.manage` دارند دسترسی دارند. رنگ‌های کم‌کنتراست
(کمتر از ۴٫۵ به ۱ طبق WCAG AA) ذخیره نمی‌شوند. این تغییرات فقط رکورد `ThemeConfig` را عوض می‌کنند و هیچ نوشته،
صفحه یا رسانه‌ای را تغییر نمی‌دهند یا حذف نمی‌کنند.

### سرعت، دسترس‌پذیری و سئو

- قالب پیش‌فرض بدون جاوااسکریپت است؛ تصاویر `loading="lazy"` دارند و فونت `font-display: swap` + preload است.
- پاسخ‌ها gzip و دارای `ETag` هستند؛ فایل‌های قالب با `?v=<version>` کش می‌شوند.
- تعداد کوئری‌های دیتابیس به تعداد نوشته‌های فهرست وابسته نیست.
- پیوند «پرش به محتوا»، ساختار عنوان‌ها (یک `h1` در هر صفحه)، `aria-current`، فوکوس مرئی، `prefers-reduced-motion`.
- `<title>`، توضیحات، canonical، Open Graph/Twitter، JSON-LD، `sitemap.xml`، `robots.txt`؛ صفحه‌های جستجو و ۴۰۴ `noindex`.

### چک‌لیست قبل از انتشار قالب

1. `python manage.py check_themes <slug>` بدون خطا.
2. در `base.html` هر دو تگ `theme_head` و `theme_toolbar` هست و رنگ‌ها فقط از `--rv-*` می‌آیند.
3. صفحهٔ اصلی با بخش‌های خالی و بدون نوشته خراب نمی‌شود (حالت خالی).
4. در موبایل (۳۶۰px) و دسکتاپ بررسی شده و با صفحه‌کلید قابل پیمایش است.
5. `version` بالا رفته و `README.md`/`LICENSE` در پوشه هست.

---

## ۳. نصب قالب از فایل ZIP (پنل)

مدیرکل می‌تواند از «قالب‌ها ← بارگذاری قالب (ZIP)» یک قالب Ravix را بارگذاری و نصب کند. **پیش‌فرض خاموش است** و فقط با تنظیم آگاهانهٔ سرور کار می‌کند.
قالب‌های وردپرس و کد PHP/پایتون پذیرفته نمی‌شوند؛ این قابلیت فقط قالب‌های Ravix (قرارداد `api_version` 1) را نصب می‌کند.

### فعال‌سازی (مدیر سرور)

```bash
sudo mkdir -p /srv/ravin/themes_installed
sudo chown ravin:ravin /srv/ravin/themes_installed && sudo chmod 750 /srv/ravin/themes_installed   # دیسک محلی
# در /etc/ravin/ravin.env :
RAVIN_THEME_INSTALL_DIR=/srv/ravin/themes_installed
RAVIN_THEME_WEB_INSTALL=true
sudo systemctl restart ravin
```

- پوشهٔ نصب جدا از کد برنامه است (کد برای سرویس فقط‌خواندنی می‌ماند) و خودکار به `RAVIN_THEME_DIRS` اضافه می‌شود. سرویس فقط در همین پوشه (و `media`) می‌نویسد.
- پوشه باید روی **دیسک محلی** باشد. قفل نصب با `flock` فایل‌سیستمی است؛ روی NFS/CIFS نصب رد می‌شود (بدون جایگزین ناامن). اگر `flock` کار نکند هم نصب انجام نمی‌شود.
- قابلیت `themes.install` فقط به مدیرکل (`SUPER_ADMIN`) داده می‌شود؛ مدیر معمولی (`themes.manage`) فقط قالب‌ها را فعال/پیش‌نمایش/سفارشی می‌کند.
- Nginx: `deploy/nginx-ravin.conf` برای مسیر `/panel/themes/upload/` سقف ۱۱MB و `limit_req` دارد. سقف واقعی را خود برنامه اعمال می‌کند (Nginx به‌تنهایی کافی نیست).
- پشتیبان‌گیری: `deploy/backup.sh` پوشهٔ `themes_installed` را هم می‌گیرد و `restore.sh` آن را برمی‌گرداند.

### ساختار ZIP

```
my-theme.zip
└── my-theme/                  ← یک پوشهٔ ریشه؛ نام آن = شناسهٔ قالب = "slug" در theme.json
    ├── theme.json
    ├── templates/             ← فقط .html؛ ۹ قالب الزامی (base، home، post_list، post_detail، category_detail، tag_detail، page_detail، search، 404)
    ├── static/                ← css، js، تصویر، فونت (فایل .html ممنوع)
    ├── README.md
    └── LICENSE
```

آماده‌سازی: همان ساختار بخش ۱ را بسازید و `python manage.py check_themes my-theme` را اجرا کنید؛ سپس پوشه را **با خود پوشه** ZIP کنید
(نه فقط محتوای آن؛ در macOS پوشهٔ `__MACOSX` را نگذارید).

### محدودیت‌ها (در سرور اعمال می‌شود)

| مورد | حد |
|---|---|
| حجم فایل ZIP | ۱۰ مگابایت |
| مجموع حجم فایل‌های بازشده | ۳۰ مگابایت (نسبت فشرده‌سازی بیش از ۱۰۰ برابر = رد) |
| تعداد فایل | ۴۰۰ (حداکثر ۸۰۰ ورودی با پوشه‌ها) |
| حجم هر فایل | ۵ مگابایت |
| طول/عمق مسیر | ۲۰۰ نویسه / ۸ سطح |
| نام فایل و پوشه | فقط حروف انگلیسی، عدد، `.`، `-`، `_`؛ بدون فاصله، فارسی و نقطهٔ ابتدایی |
| پسوندهای مجاز | html css js json txt md png jpg jpeg gif webp ico woff woff2 (و `LICENSE` بدون پسوند در ریشه) |
| **SVG** | **در نسخهٔ اول رد می‌شود** (محتوای فعال در SVG پاک‌سازی نمی‌شود؛ از png/webp/jpg استفاده کنید) |
| py، php، sh، exe و …، فایل رمزگذاری‌شده، symlink و ورودی ویژه | رد (رد امنیتی در ممیزی) |

### مراحل در پنل

1. «قالب‌ها» ← کارت «بارگذاری قالب (ZIP)» ← انتخاب فایل ← «بارگذاری و نصب».
2. سرور فهرست ZIP را بدون استخراج می‌سنجد، سپس در پوشهٔ موقتِ تصادفی استخراج می‌کند، با همان `inspect_theme_dir` (مبنای `check_themes`) اعتبارسنجی می‌کند و فقط در صورت موفقیت با `rename` اتمی نصب می‌کند.
3. قالب **خودکار فعال نمی‌شود**؛ در فهرست ظاهر می‌شود و با «پیش‌نمایش/فعال‌سازی» همان مسیر قبلی را می‌رود.
4. قالبی که از قبل وجود دارد (هر پوشه‌ای با همان نام در هر مسیر قالب) و قالب `default` **هرگز** بازنویسی نمی‌شود. برای ارتقا از به‌روزرسانی امضاشده (`docs/upgrade.md`) استفاده کنید.
5. هر درخواست در «گزارش رویدادها» (نوع «ظاهر سایت») ثبت می‌شود: آغاز، موفق، رد، رد امنیتی یا خطای داخلی (با پیشوند «نصب قالب ZIP —»). نام فایل کاربر و اطلاعات محرمانه ثبت نمی‌شود.

### خطاهای رایج

| پیام | علت و راه‌حل |
|---|---|
| نصب از طریق وب خاموش است | `RAVIN_THEME_WEB_INSTALL=true` و راه‌اندازی مجدد سرویس |
| فقط یک پوشهٔ ریشه … | فایل‌ها را داخل یک پوشه به نام شناسهٔ قالب بگذارید و دوباره ZIP کنید |
| نام «…» مجاز نیست | نام فایل/پوشه فارسی، با فاصله یا با نقطهٔ ابتدایی است |
| فایل SVG در این نسخه مجاز نیست | SVG را به png/webp تبدیل کنید |
| قالب با قرارداد سازگار نیست + فهرست خطا | همان خطاهای `check_themes`؛ اصلاح و دوباره ZIP کنید |
| قالبی با این شناسه از قبل وجود دارد | شناسه را (در theme.json و نام پوشه) تغییر دهید؛ جایگزینی انجام نمی‌شود |
| نصب دیگری در جریان است | چند ثانیه بعد دوباره تلاش کنید (قفل با پایان یا مرگ فرایند آزاد می‌شود) |
| پوشهٔ نصب قابل‌نوشتن نیست / قفل پشتیبانی نمی‌شود | مالک/مجوز `RAVIN_THEME_INSTALL_DIR` و نوع دیسک (محلی) را بررسی کنید |

### فایل‌های استاتیک پس از نصب (نتیجهٔ آزمایش واقعی)

نصب از پنل **`collectstatic` را اجرا نمی‌کند**. آزمایش در یک کپی ایزوله (تنظیمات production، Gunicorn ۲ worker + WhiteNoise، و Nginx با `alias` به `staticfiles/` مطابق `deploy/nginx-ravin.conf`):

| مرحله | WhiteNoise (فقط Gunicorn) | Nginx |
|---|---|---|
| قبل از نصب (قالب جدید) | ۴۰۴ | ۴۰۴ |
| پس از نصب، بدون `collectstatic` | ۴۰۴ | ۴۰۴ |
| پس از `collectstatic` (فرایند تازه)، بدون راه‌اندازی مجدد | ۴۰۴ | **۲۰۰** |
| پس از reload (HUP) یا restart سرویس | ۲۰۰ | ۲۰۰ |

(در تمام مراحل استاتیک قالب `default` ۲۰۰ ماند.) نتیجه:
- با Nginx در جلو (روش مستند این پروژه): فقط یک بار `sudo -u ravin /srv/ravin/venv/bin/python manage.py collectstatic --noinput` کافی است؛ restart لازم نیست.
- بدون Nginx (WhiteNoise به‌تنهایی): پس از `collectstatic` سرویس را reload/restart کنید.
- پیش از اجرای `collectstatic`، صفحه‌های قالب بدون CSS/JS کامل نمایش داده می‌شوند. پنل تا زمان انتشار استاتیک هشدار «استاتیک هنوز منتشر نشده» نشان می‌دهد (وجود `STATIC_ROOT/themes/<slug>/` سنجیده می‌شود).
- بین نصب و `collectstatic` بهتر است قالب را فعال نکنید.

### نکات امنیتی برای توسعه‌دهندگان قالب

- قالب‌ها با موتور قفل‌شده رندر می‌شوند (فقط `{% load ravin_theme %}`)؛ کدی اجرا نمی‌شود. فایل پایتون/PHP هرگز پذیرفته نمی‌شود.
- جاوااسکریپت فقط فایل جدا در `static/` باشد (CSP: `script-src 'self'`).
- فایل‌های قالب مستقیم از وب سرو می‌شوند؛ چیز محرمانه (کلید، رمز) در قالب نگذارید.
- فقط قالب‌های منبع مورد اعتماد را نصب کنید؛ تگ‌های داخلی Django مثل `{% url %}` در موتور قالب باز هستند.
