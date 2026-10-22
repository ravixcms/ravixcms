# Changelog
قالب: [Keep a Changelog](https://keepachangelog.com/)، نسخه‌بندی [SemVer](https://semver.org/).

## [Unreleased]
### Fixed
- ورود به پنل با کش اصلی خراب (پوشهٔ `/var/cache/ravin` نبود/قابل‌نوشتن نبود، Redis قطع بود) خطای ۵۰۰ می‌داد، چون محدودسازی ورود (`apps/accounts/throttle.py`) در هر تلاش از کش می‌خواند. اکنون در این حالت به کش جدولی دیتابیس (`throttle_fallback`، جدول `ravin_cache`) برمی‌گردد و هشدار لاگ می‌دهد (حداکثر یک بار در دقیقه)؛ محافظت در برابر حدس رمز خاموش نمی‌شود. اگر کش جایگزین هم خراب باشد خطا بالا می‌آید.
### Changed
- **`deploy/backup.sh`: نگهداری بر پایهٔ تعداد، نه سن.** `KEEP_DAYS` (۱۴ روز) حذف شد و `KEEP_COUNT` (پیش‌فرض ۷) جایگزین آن است: فقط ۷ پشتیبان کامل آخر می‌مانند. اگر `KEEP_DAYS` را در `ravin.env` گذاشته‌اید دیگر خوانده نمی‌شود. `KEEP_COUNT` نامعتبر (غیرعدد یا ۰) اجرا را متوقف می‌کند.
### Added (دیدگاه‌ها)
- اپ `apps.comments`: مدل `Comment` (وضعیت‌ها: در انتظار/تأییدشده/اسپم/زباله؛ پاسخ تودرتو تا عمق ۲ — `RAVIN_COMMENT_MAX_DEPTH`)، ثبت عمومی در `POST /blog/<slug>/comment/`، و صفحهٔ مدیریت «دیدگاه‌ها» (تب وضعیت، جستجو، عملیات گروهی: تأیید/در انتظار/اسپم/زباله/حذف دائمی/خالی‌کردن زباله) با capability `comments.moderate` (مدیر، مدیرکل) و لاگ ممیزی.
- `Post.comments_open` (بستن دیدگاه برای هر نوشته)؛ `SiteSettings.comments_require_approval` (پیش‌فرض روشن)، `comments_notify_admin`، `comments_blocked_words`. مهاجرت‌ها: `comments.0001`، `content.0004`، `core.0005`.
- ضداسپم: honeypot (پاسخ یکسان با «در انتظار»)، محدودیت نرخ به‌ازای IP (`RAVIN_COMMENT_LIMIT_PER_IP`/`RAVIN_COMMENT_WINDOW_SECONDS`، با کش جایگزین دیتابیس)، فهرست کلمات مسدود (⇒ اسپم)، تشخیص دیدگاه تکراری، بررسی Origin. متن با nh3 پاک‌سازی می‌شود (فقط لینک http/https با `nofollow ugc`، پررنگ، مورب، کد، نقل‌قول).
- ایمیل اطلاع به «ایمیل مدیر» برای دیدگاه تازهٔ غیراسپم (خطای SMTP دیدگاه را رد نمی‌کند).
- hookهای افزونه: `comments.before_save` (filter؛ فقط `status` به `pending`/`spam`)، `comments.created` و `comments.status_changed` (action).
- قالب پیش‌فرض (نسخهٔ ۱.۱.۰): بخش دیدگاه‌ها، فرم، پاسخ بدون JS؛ متغیر `comments` در قرارداد `post_detail.html` (DTO: `CommentItem`، `CommentsSection`).
- ۵۰ آزمون در `apps/comments/tests/test_comments.py`.
### Added (SEO / Open Graph / Redirect)
- `Post.seo_title`، `Post.seo_description`، `Post.noindex` (مثل صفحه؛ فرم پنل بخش «سئو و اشتراک‌گذاری»). نوشتهٔ noindex متای `robots: noindex,follow` می‌گیرد و از `sitemap.xml` حذف می‌شود. مهاجرت `content.0003`.
- `SiteSettings.default_share_image` (تنظیمات سایت) و مهاجرت `core.0004`: وقتی تصویر شاخص نیست، `og:image` از آن می‌آید.
- Open Graph/Twitter Card کامل در `theme_head`: `article:published_time/modified_time`، `twitter:title/description/image`؛ `make_seo` پارامترهای `published_time`/`modified_time` گرفت. `finish_seo` پس از hookهای افزونه `og_image` نسبی را مطلق می‌کند.
- مدل `Redirect` (مسیر قدیم یکتا، مسیر جدید داخلی یا http(s)، ۳۰۱/۳۰۲) + صفحهٔ پنل «ریدایرکت‌ها» + capability `redirects.manage` (مدیر، مدیرکل) + لاگ ممیزی؛ `RedirectFallbackMiddleware` فقط روی پاسخ ۴۰۴ GET/HEAD، با حفظ query، و بدون اثر روی مسیرهای سیستمی.
- تغییر نامک نوشته یا صفحهٔ منتشرشده خودکار ریدایرکت ۳۰۱ می‌سازد (بدون زنجیره؛ برای پیش‌نویس نه).
- ۵۲ آزمون جدید، از جمله سازگاری با افزونهٔ `seo_toolkit`.
### Added
- **فید RSS 2.0 و Atom** با `django.contrib.syndication`: `/feed/` (همهٔ نوشته‌ها)، `/category/<slug>/feed/` (با زیردسته‌ها) و `/tag/<slug>/feed/`؛ نسخهٔ Atom هرکدام با `atom/` در انتها (`/feed/atom/` و …). فقط نوشته‌های منتشرشده (شامل زمان‌بندی‌شدهٔ رسیده)، جدیدترین اول، `RAVIN_FEED_ITEMS` مورد (پیش‌فرض ۲۰). تاریخ انتشار `publish_at` است (نه زمان ساخت)، به‌روزرسانی `updated_at`، هر دو با offset درست؛ `Last-Modified`/`ETag` و پاسخ ۳۰۴. نشانی‌ها مطلق و در صورت تنظیم از `RAVIN_SITE_URL`.
- `theme_head` پیوند autodiscovery فید را در `<head>` می‌گذارد (قالب پیش‌فرض و هر قالبی که `theme_head` دارد)؛ متغیر `feeds` به قرارداد قالب افزوده شد (`FeedLink`).
- نامک «feed» برای صفحه‌های ثابت رزرو شد (`RESERVED_PAGE_SLUGS`)؛ صفحهٔ ثابتی که از قبل `/feed/` داشته باشد، دیگر نمایش داده نمی‌شود (فید جای آن را می‌گیرد). ۲۵ آزمون در `apps/public/tests/test_feeds.py`.
- تایمر و سرویس systemd برای `publish_scheduled` (هر ۱ دقیقه) و پشتیبان روزانه (۰۳:۱۷، `Persistent=true`): `deploy/ravin-publish-scheduled.{service,timer}` و `deploy/ravin-backup.{service,timer}`؛ توضیح در `docs/deployment.md`. `ravin_preflight` فعال‌بودن هر سه تایمر (با فروشگاه، `expire-orders` هم) را بررسی می‌کند؛ تایمر غیرفعال خطا و نبودن systemd هشدار است.
- ۱۶ آزمون در `apps/core/tests/test_deploy_timers.py` (از جمله اجرای `backup.sh` واقعی با `pg_dump` جعلی و `systemd-analyze verify`).
- **بازیابی رمز با ایمیل** (`/accounts/password-reset/`) با viewهای داخلی Django و قالب‌های فارسی؛ پیوند یک‌بارمصرف (وابسته به هش رمز) و ۱ ساعت اعتبار (`RAVIN_PASSWORD_RESET_TIMEOUT`). پاسخ برای ایمیل موجود و ناموجود (و حساب غیرفعال، خطای SMTP و محدودیت نرخ) دقیقاً یکی است.
- **تأیید ایمیل ثبت‌نام** (`/accounts/verify-email/<token>/`): پیوند امضاشده، ۳ روز اعتبار، یک‌بارمصرف، باطل‌شدن با تغییر ایمیل؛ GET فقط صفحه را نشان می‌دهد و تأیید با POST است (اسکنر ایمیل پیوند را مصرف نمی‌کند). ارسال دوباره: `/accounts/verify-email/resend/`. تغییر ایمیل در پروفایل تأیید را از نو لازم می‌کند.
- فیلد `User.email_verified_at` + مهاجرت‌های `accounts.0003` (ستون) و `0004` (کاربران موجود «تأییدشده» علامت می‌خورند تا قفل نشوند).
- محدودسازی نرخ ارسال ایمیل به‌ازای IP (۱۰ در ساعت) و به‌ازای ایمیل (۳ در ساعت)؛ `RAVIN_MAIL_LIMIT_PER_IP`، `RAVIN_MAIL_LIMIT_PER_EMAIL`، `RAVIN_MAIL_WINDOW_SECONDS`.
- `RAVIN_REQUIRE_EMAIL_VERIFICATION` (پیش‌فرض خاموش): اگر روشن باشد ورود تا تأیید ایمیل ممکن نیست.
- دستور `manage.py send_test_email <ایمیل>` برای آزمودن SMTP (رمز SMTP هرگز چاپ نمی‌شود). در توسعه backend کنسولی است.
- متغیرهای `DEFAULT_FROM_EMAIL` و `EMAIL_TIMEOUT` (پیش‌فرض ۱۰ ثانیه).
- ۳۱ آزمون در `apps/accounts/tests/test_email_flows.py`.
- مدل `ThrottleCacheEntry` و مهاجرت `core.0003_throttle_cache_table` (جدول `ravin_cache`).
- `ravin_preflight`: آزمایش واقعی نوشتن/خواندن کش اصلی، قابل‌نوشتن‌بودن پوشهٔ کش فایلی، وجود جدول کش جایگزین، و خوانا‌بودن `STATIC_ROOT`.
- ۱۱ آزمون جدید (`apps/accounts/tests/test_throttle_fallback.py`)، از جمله بررسی این‌که لاگ تولید traceback خطای ۵۰۰ را در stderr (journald) می‌نویسد.

## [0.6.1] - 2026-10-22
### Security
- هدر CSP و Permissions-Policy برای همهٔ پاسخ‌ها (`SecurityHeadersMiddleware`)؛ حذف `onsubmit` درون‌خطی.
- Django admin فقط برای مدیرکل؛ مسیر قابل‌تنظیم (`RAVIN_ADMIN_PATH`).
- محدودیت ورود: ۵ شکست به‌ازای (IP، نام‌کاربری) و ۲۰ شکست به‌ازای IP؛ `X-Real-IP` فقط با `RAVIN_TRUST_PROXY_HEADERS=true` پذیرفته می‌شود.
- Nginx: سقف بدنهٔ جداگانه برای آپلود رسانه (۶۰MB) و فایل دیجیتال فروشگاه (۲۱۰MB).
### Fixed
- `deploy/ravin.service`: تعارض `PrivateTmp=true` با `ReadWritePaths=/var/tmp/ravin-cache` که سرویس را با `226/NAMESPACE` از کار می‌انداخت؛ کش پیش‌فرض اکنون `/var/cache/ravin` (با `CacheDirectory=ravin`) است. اگر `RAVIN_CACHE_DIR` را صریح تنظیم کرده‌اید، پوشهٔ آن باید برای کاربر سرویس قابل‌نوشتن باشد.
- `scripts/vps_smoke.sh`: `RAVIN_RESTORE_YES=1` اکنون با `sudo -u ravin env ...` به `restore.sh` می‌رسد (پیش‌تر `sudo` متغیر را پاک می‌کرد و بازیابی «لغو شد» می‌داد).
- `scripts/vps_smoke.sh`: مرحلهٔ PostgreSQL دوباره‌اجرا‌پذیر شد (نقش موجود به‌جای خطا، رمزش به‌روز می‌شود).
- بازیابی تاریخچهٔ به‌روزرسانی روی PostgreSQL ترتیب (sequence) را به‌روز نمی‌کرد ⇒ خطای کلید تکراری.
- تست‌های ناپایدار (`test_constant_query_count_on_lists`، `shop … test_queries_constant_on_list`، `OversellRaceTests` روی PostgreSQL).
### Changed
- `deploy/backup.sh` / `restore.sh`: پشتیبان shop_private، افزونه‌ها و registry؛ فهرست هش و بررسی پیش از بازیابی.
### Added
- بستهٔ انتشار کاربر نهایی: `.gitattributes` (حذف آزمون‌ها، `.github` و اسکریپت‌های e2e از آرشیو) و `scripts/verify_release.py` (راستی‌آزمایی محتوا، اسرار، نسخه و SHA-256) که در `release.yml` اجرا می‌شود.
- نصب قالب از فایل ZIP در پنل (فقط مدیرکل، قابلیت `themes.install`، پیش‌فرض خاموش با `RAVIN_THEME_WEB_INSTALL`): `apps/themes/package.py` و `installer.py`، پوشهٔ نصب جدا `RAVIN_THEME_INSTALL_DIR`، قفل فایل‌سیستمی، نصب اتمی، بدون جایگزینی قالب موجود/`default`، SVG در نسخهٔ اول رد می‌شود، ثبت در ممیزی؛ مهر نسخهٔ registry برای workerها؛ پشتیبان‌گیری از `themes_installed`؛ مسیر Nginx با سقف جدا. مستندات و نتیجهٔ آزمایش استاتیک: `docs/themes.md` بخش ۳.
- `scripts/e2e_backup_restore.py`، `requirements.lock`، CI و گردش کار انتشار GitHub، مستندات انتشار/میزبانی، LICENSE.
- `scripts/vps_smoke.sh` (آزمون دودی استقرار روی Ubuntu تازه؛ هنوز روی VPS واقعی اجرا نشده)، `scripts/vps_smoke_check_log.sh` و `docs/vps-smoke-runbook.md`.

## [0.6.0]
- هسته، محتوا، سایت عمومی، صفحه‌ساز، افزونه‌ها، قالب‌ها، فروشگاه، چندسایتی، به‌روزرسانی امضاشده.
