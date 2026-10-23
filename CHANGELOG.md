# Changelog
قالب: [Keep a Changelog](https://keepachangelog.com/)، نسخه‌بندی [SemVer](https://semver.org/).

## [Unreleased]
### Added (چندزبانگی؛ ۱۰ زبان رابط: fa، en، ar، tr، es، fr، de، ru، pt-br، zh-hans)
- **همهٔ متن‌های رابط** (پنل، قالب پیش‌فرض، ایمیل‌ها، پیام‌های خطا و اعتبارسنجی، نصب‌کننده، افزونه‌های همراه، جاوااسکریپت) با gettext علامت‌گذاری شد؛ msgidها انگلیسی‌اند و کاتالوگ `fa` ترجمهٔ فارسی قبلی است. فقط رابط ترجمه می‌شود، نه محتوای کاربر. کاتالوگ‌ها در `locale/` (و `.mo`ها در گیت).
- **جهت خودکار**: `rtl` برای `fa`/`ar` و `ltr` برای بقیه؛ CSS پنل و قالب با ویژگی‌های منطقی (`inline-start/end`) در هر دو جهت کار می‌کند.
- **تقویم و رقم**: جلالی و رقم فارسی فقط برای `fa`؛ بقیه میلادی و رقم لاتین (`apps/core/localefmt.py`). فونت به‌ازای خط: Vazirmatn (فارسی/عربی)، Inter (لاتین/سیریلیک)، Inter + فونت سیستم (چینی).
- **انتخاب زبان**: گزینشگر در پاورقی سایت و در پنل/صفحات ورود؛ ذخیره در کوکی `ravix_lang` و نمایهٔ کاربر (`User.language`)؛ «زبان پیش‌فرض سایت» در تنظیمات سایت. ترتیب: پیشوند آدرس ← کوکی ← نمایه ← پیش‌فرض سایت ← فارسی.
- **پیشوند زبان در آدرس سایت عمومی و فروشگاه** (`/fa/…`، `/en/…`، `/de/…`) با `hreflang` (و `x-default`) و canonical دارای پیشوند؛ `/sitemap.xml` فهرست sitemap هر زبان (`/<lang>/sitemap.xml` با `xhtml:link`) است و `robots.txt` در ریشه می‌ماند.
- ترجمهٔ قالب (`locale/<lang>.json`، کامپایل‌شده توسط Ravix) و افزونه (`locale/<lang>/LC_MESSAGES/django.mo`) با `apps/core/catalogs.py`؛ ترجمهٔ هسته همیشه اولویت دارد. راهنما: `docs/translation.md`.
- آزمون `apps.core.tests.test_i18n_completeness`: شکست برای متن علامت‌نخورده (فارسیِ پایتون، متن/ویژگی‌های دیدنی قالب‌ها)، رشتهٔ علامت‌خورده‌ای که در کاتالوگ نیست، ترجمهٔ خالی/fuzzy/منسوخ، ناهماهنگی رشته‌ها بین زبان‌ها، جانگهدار جاافتاده و `.mo` کهنه.
- `name` و `description` در `theme.json` و `plugin.json` اختیاراً چندزبانه (`{"fa": "…", "en": "…"}`)؛ نمایش به زبان فعال با بازگشت به متن پیش‌فرض (`en`). قالب پیش‌فرض و سه افزونهٔ همراه برای هر ۱۰ زبان نام و توضیح دارند (نسخه‌ها ۱.۰.۱ / ۱.۱.۱ شد، چون اثر انگشت افزونه با تغییر فایل عوض می‌شود).
- ارتقا: برگه‌ها و نوشته‌هایی که نامکشان کد زبان است خودکار تغییر نام می‌دهند (مثلاً `de-page`) با ریدایرکت ۳۰۱ و ثبت در لاگ ممیزی (migration، `ravix_fix_slugs`، سلامت سایت و preflight).
### Fixed
- بررسی بصری ۱۰ زبان × دسکتاپ/موبایل: ستون کناری ویرایش نوشته و داشبورد موبایل دیگر از صفحه بیرون نمی‌زنند؛ متن Markdown و دکمه‌های نوارابزار در راست‌به‌چپ درست نمایش داده می‌شوند.
### Changed (ناسازگار)
- **آدرس‌های سایت عمومی پیشوند زبان دارند.** آدرس‌های بدون پیشوند (`/blog/x/`) با ریدایرکت کار می‌کنند (۳۰۲ برای زبان انتخابی بازدیدکننده، ۳۰۱ برای پیش‌فرض سایت)؛ ریدایرکت‌های خودکار تغییر نامک بدون پیشوند ذخیره می‌شوند. کدهای زبان (`fa`، `en`، `de`، …) نامک رزروشده‌اند؛ صفحه‌ای که قبلاً با چنین نامکی ساخته شده بود باید نامک دیگری بگیرد.
- پیوند داخلی نوشته‌شده توسط مدیر (منو، دکمهٔ CTA، دکمهٔ hero) خودکار پیشوند زبان جاری را می‌گیرد؛ پیوند داخل متن نوشته‌ها دست‌نخورده می‌ماند و از ریدایرکت عبور می‌کند.
- کلیدهای مرتبط با `fa_digits` سازگار می‌مانند؛ نام‌های زبان‌محور `local_digits`، `local_date`، `local_datetime`، `money` اضافه شد. بستهٔ قالب می‌تواند پوشهٔ `locale/` داشته باشد.
### Notes
- خروجی خط فرمان (دستورهای `manage.py`)، پیام‌های راه‌اندازی تنظیمات (`config/settings`)، لاگ‌ها و کامنت‌های کد فارسی مانده‌اند (برای اپراتور سرور، نه کاربر نهایی).

## [0.9.0] - 2026-10-23
نخستین نسخهٔ نامزد انتشار عمومی؛ تغییرات زیر شامل بستن موانع فهرست `docs/RELEASE_BLOCKERS.md` است.
### Security (IP کلاینت، مانع ۱-۱)
- **تعیین IP در یک تابع** (`apps/core/clientip.py`) و همهٔ مصرف‌کننده‌ها (محدودیت ورود، دیدگاه، ایمیل، لاگ ممیزی، فروشگاه، pluginhub، API افزونه‌ها) فقط از همان استفاده می‌کنند. `X-Forwarded-For`/`X-Real-IP` فقط وقتی خوانده می‌شود که درخواست از **سوکت یونیکس** (Gunicorn پشت Nginx؛ چیدمان پیشنهادی، بدون هیچ تنظیمی) یا از نشانی/شبکهٔ فهرست `RAVIX_TRUSTED_PROXIES` بیاید؛ در زنجیرهٔ XFF **راست‌ترین IP غیرمورداعتماد** برداشته می‌شود تا جعل هدر توسط مهاجم اثری نداشته باشد.
- اگر IP قابل‌تشخیص نباشد (`None`)، محدودیت سراسری IP اعمال **نمی‌شود** و فقط محدودیت به‌ازای نام‌کاربری/ایمیل می‌ماند؛ قبلاً همهٔ بازدیدکننده‌ها یک «IP» می‌شدند و ۲۰ ورود ناموفق ورود همه را قفل می‌کرد.
- «سلامت سایت» (بررسی زنده) و `ravix_preflight` (مشاهدات اخیر + اعتبار تنظیم) وضعیت را گزارش می‌کنند: پراکسی نامورداعتماد ← بحرانی، نبود هدر IP ← پیشنهادی.
### Changed (ناسازگار)
- **`RAVIX_TRUST_PROXY_HEADERS` حذف شد** (اثری ندارد؛ preflight هشدار می‌دهد). جایگزین: `RAVIX_TRUSTED_PROXIES` (IP/CIDR با کاما). پشت Nginx روی سوکت یونیکس چیزی لازم نیست؛ Nginx روی TCP: `RAVIX_TRUSTED_PROXIES=127.0.0.1`. `docker-compose.yml` شبکه‌های خصوصی Docker را مورداعتماد می‌کند.
- `SECURE_HSTS_PRELOAD` و `SECURE_HSTS_INCLUDE_SUBDOMAINS` پیش‌فرض **خاموش** (برگشت‌ناپذیرند)؛ با `DJANGO_HSTS_PRELOAD` / `DJANGO_HSTS_INCLUDE_SUBDOMAINS` روشن می‌شوند.
- افزونه‌های همراه بسته (`contact_form`, `seo_toolkit`, `example_pay`) و قالب `startplugin`: `"ravix": ">=0.9,<2.0"` (قبلاً `<1.0.0`، یعنی روی ۱.۰ نصب نمی‌شدند). آزمون تازه سازگاری با نسخهٔ جاری و 1.x را می‌سنجد.
- `RAVIX_VERSION = 0.9.0` (و `RAVIN_VERSION` قدیمی هم‌ارز).
### Changed (حقوقی و امنیتی)
- `LICENSE`: دارندهٔ حق نشر «The Ravix Authors» (MIT بدون تغییر). `SECURITY.md`: راه تماس ravixcms@gmail.com، گزارش خصوصی، پاسخ اولیه ۷ روز، نسخه‌های پشتیبانی‌شده.
### Fixed
- **خطای ۵۰۰ با ورودی عددی بزرگ/غیرمعمول** (مانع ۱-۶): `apps/core/params.py` (`safe_id`/`safe_ids`/`safe_int`: فقط رقم ASCII، سقف INTEGER دیتابیس) در دیدگاه‌ها (فیلتر `post`، عملیات گروهی)، فیلترهای نوشته (`category`/`tag`/`author`)، سازندهٔ صفحه، پاسخ دیدگاه، سبد فروشگاه و مدیریت سایت‌ها. `NumericOverflowMiddleware` عددهای بیش از بازه در مسیرهای دیگر (مثلاً فیلترهای خود django-admin که با fuzz پیدا شد) را به ۴۰۰ فارسی تبدیل می‌کند. رقم‌های غیر ASCII مثل «²» هم قبلاً با `isdigit()` به `int()` می‌رسیدند و ۵۰۰ می‌دادند.
- آزمون ناپایدار `test_token_shown_once_and_revocable` (گاهی به‌خاطر حروف تصادفی توکن) پایدار شد: کل توکن در صفحهٔ لیست جست‌وجو می‌شود، نه یک پیشوند ثابت.
### Added (آزمون و CI)
- `config/testrunner.py`: کل اجرای آزمون‌ها با `MEDIA_ROOT` موقت؛ دیگر در `media/` پروژه فایل نمی‌ماند.
- آزمون‌های تازه: IP (جعل هدر، زنجیرهٔ XFF، پراکسی نامورداعتماد، IP نامعلوم، سلامت/preflight)، fuzz عددی روی همهٔ مسیرهای بدون آرگومان با مقدارهای مخرب، محیط آزمون و HSTS.
- job «secrets» در `ci.yml`: `gitleaks` روی کل تاریخچه با `.gitleaks.toml` (فقط دادهٔ ساختگی آزمون‌ها مجاز است؛ باینری با checksum بررسی می‌شود).
### Changed (آزمون)
- آزمون‌های updater «سایت در حال اجرا» را صریحاً `0.6.1` فرض می‌کنند (دیگر به نسخهٔ واقعی مخزن وابسته نیستند).
### Added (سرور رسمی به‌روزرسانی و مخزن)
- `apps/core/official.py`: تنها نقطهٔ ویرایش ناشر برای آدرس‌های پیش‌فرض (`OFFICIAL_UPDATE_URL`، `OFFICIAL_REGISTRY_URL`)، کلیدهای عمومی رسمی (`OFFICIAL_PUBLIC_KEYS`) و کلیدهای ابطال‌شده (`REVOKED_KEY_IDS`). `RAVIX_UPDATE_URL` و `RAVIX_OFFICIAL_REGISTRY_URL` پیش‌فرض را عوض می‌کنند (مقدار خالیِ صریح = خاموش). همه خالی ارسال می‌شوند.
- ابطال کلید: `RAVIX_REVOKED_KEY_IDS` (env) و `official.REVOKED_KEY_IDS` بر همهٔ منبع‌های کلید (کد، env، پنل) غالب‌اند؛ پنل «کلیدهای مورد اعتماد» کلید رسمی داخل کد و کلیدهای ابطال‌شده را نشان می‌دهد. `ravix_release keygen` اکنون `key_id` را هم چاپ می‌کند (کلید عمومی همچنان آخرین خط است).
- مستند: راهنمای کامل راه‌اندازی سرور رسمی به‌روزرسانی (`docs/release.md`) و مخزن افزونه‌ها (`docs/registry.md`): کلید آفلاین، قراردادن کلید در کد، چرخش، ابطال، آدرس و env، رفتار هنگام قطع.
### Fixed (سرور رسمی)
- **دانلود بستهٔ هسته/قالب از `RAVIX_UPDATE_URL` با فهرست ساخته‌شده توسط `ravix_release` کار نمی‌کرد**: `download_url` فقط نام فایل بود و به‌اشتباه «دامنهٔ دیگر» شمرده می‌شد. اکنون نسبت به `<URL>/updates/v1/` حل می‌شود (نشانی مطلق باید همان دامنه و https باشد).
- قطع بودن سرور به‌روزرسانی هر بازشدن صفحهٔ «به‌روزرسانی‌ها» را تا ۳۰ ثانیه معطل می‌کرد: اکنون سقف انتظار فهرست ۸ ثانیه و شکست ۵ دقیقه کش می‌شود (دکمهٔ «بررسی» کش را دور می‌زند).
### Changed (CI و انتشار)
- `ci.yml`: ماتریس Python 3.12/3.13 × SQLite/PostgreSQL 16 (+ بدون فروشگاه)، job جداگانهٔ بررسی migration جاافتاده، E2E روی PG16، `pip-audit --strict`، `bandit` با baseline (`scripts/bandit_check.sh`، `.bandit-baseline.json`)، `check --deploy` و ساخت/بررسی image داکر. `workflow_call` برای استفادهٔ release.
- `release.yml`: preflight (تگ = نسخه، بخش CHANGELOG، commit روی main) ← CI کامل ← zip + `SHA256SUMS` + `verify_release.py` و image GHCR (provenance/SBOM) ← GitHub Release (pre-release برای تگ دارای `-`). **امضا در CI نیست**؛ امضای دستی و آفلاین در `docs/release.md`.
### Added (بعد از نصب)
- **محتوای نمونه** (`apps/content/samples.py`): هنگام نصب وب/CLI یک نوشته («به راویکس خوش آمدید») و برگه‌های «درباره ما» و «تماس با ما» ساخته می‌شود و دو برگه به منوی سربرگ می‌روند. `Post.is_sample`/`Page.is_sample` (مهاجرت `content.0005`)؛ با اولین ویرایش در پنل، علامت نمونه برمی‌افتد. دکمهٔ «حذف محتوای نمونه» در داشبورد فقط نمونه‌های ویرایش‌نشده را پاک می‌کند (نیازمند `content.publish` و `pages.manage`، ثبت در گزارش ممیزی). خطا در ساخت نمونه نصب را خراب نمی‌کند.
- **راهنمای شروع** در داشبورد (لوگو ← اولین نوشته ← قالب): وضعیت هر گام از داده‌های واقعی تشخیص داده می‌شود (نوشتهٔ نمونه حساب نمی‌شود)، با «بستن راهنما» (`SiteSettings.onboarding_dismissed`، مهاجرت `core.0008`). فقط برای `settings.manage`.
- **صفحه‌های خطا:** ۴۰۴ قالب پیش‌فرض بازطراحی شد؛ ۵۰۰ و حالت تعمیر اکنون صفحهٔ HTML فارسی **مستقل** هستند (CSS درون‌خطی؛ بدون دیتابیس، static و context processor، پس وقتی همه‌چیز خراب است هم نمایش داده می‌شوند). حالت تعمیر ۵۰۳ با `Retry-After` و بازخوانی خودکار برمی‌گرداند؛ درخواست‌های غیر HTML همان متن ساده را می‌گیرند.
- **سلامت سایت** (`/panel/site-health/`، capability تازهٔ `health.view` برای مدیر/مدیرکل): بررسی HTTPS/HSTS/DEBUG، ایمیل، کش، دیتابیس، پوشهٔ رسانه و دیسک، timerهای systemd (+ نوشتهٔ زمان‌بندی‌شدهٔ عقب‌افتاده)، تازگی پشتیبان (`BACKUP_DIR`، `/var/backups/ravix`، `/backups/.last_ok`)، نسخهٔ پایتون/Django/راویکس، مهاجرت‌های اجرانشده، قالب و افزونه. سطح‌ها: خوب/پیشنهادی/بحرانی/نامشخص (وقتی چیزی قابل سنجش نیست، مثل systemd در Docker). دکمهٔ «ارسال ایمیل آزمایشی» فقط به ایمیل خود کاربر و با محدودیت ۳۰ ثانیه.
### Added (روش‌های نصب)
- **نصب‌کنندهٔ وب** (`apps/installer`، `/install/`): نام سایت، مدیرکل، زبان و منطقهٔ زمانی؛ مهاجرت‌ها در صورت نیاز همان‌جا اجرا می‌شوند؛ مدیرکل و تنظیمات در یک تراکنش ساخته می‌شوند و صفحه پس از نصب برای همیشه ۴۰۴ است. در production با توکن نصب (`RAVIX_INSTALL_TOKEN` یا فایل/لاگ؛ `manage.py ravix_install_token`). سایتی که کاربر دارد هرگز نصب‌پذیر نیست. دستور بدون‌وب `ravix_install`. خاموش‌کردن: `RAVIX_INSTALLER_ENABLED=false`. مهاجرت `core.0007` (`SiteSettings.language/timezone/installed_at`).
- **زبان و منطقهٔ زمانی سایت** در «تنظیمات سایت» و در هر درخواست فعال می‌شود (`SiteRegionalMiddleware`؛ ذخیرهٔ تاریخ‌ها UTC می‌ماند). فرم‌های قدیمی که این دو را نمی‌فرستند مقدار فعلی را نگه می‌دارند.
- **Docker:** `Dockerfile` چندمرحله‌ای (کاربر غیر root، کد فقط‌خواندنی)، `docker-compose.yml` (ravix + PostgreSQL 17 + Redis + Nginx + سرویس پشتیبان دوره‌ای با volumeهای media/data/backups)، `docker-compose.tls.yml` (HTTPS با Let's Encrypt میزبان)، `docker/init-env.sh` (رمزهای تصادفی). کلید Django از فایل هم خوانده می‌شود (`RAVIX_SECRET_KEY_FILE`).
- **`install.sh`** برای Ubuntu 22.04/24.04: پایتون، PostgreSQL، کاربر سیستمی، systemd، Nginx و certbot؛ idempotent، پیش از هر تغییر می‌پرسد، `--dry-run`/`--yes`.
- `deploy/nginx-ravix.conf`: محدودیت نرخ برای `/install/`.
- مستند: `docs/installer.md` (جزئیات و آنچه تأیید نشده). نصب‌کنندهٔ **افزونه‌ها** که قبلاً این نام را داشت به `docs/plugin-installer.md` منتقل شد.
### Changed (تغییر نام ravin → ravix)
- نام محصول در همهٔ کد، فایل‌های استقرار و مستندات `ravix` شد (دستورهای `ravix_*`، `ravix.service`، `RAVIX_*`، `{% load ravix_theme %}`/`ravix_ui`، …). **نام‌های قدیمی تا 2.0 با `RavixDeprecationWarning` کار می‌کنند** (متغیرهای `RAVIN_*`، دستورهای `ravin_*`، تگ‌های `ravin_theme`/`ravin_ui`، کلید `ravin` در manifest/`requires`، تایمرها و مسیرهای قدیمی). فهرست کامل و راهنمای مهاجرت: `docs/upgrade.md` §۹.
- ناسازگار (بدون شیم): `settings.RAVIN_*`، نام لاگرها، کلیدهای نشست، `static/css/ravin.css` و `static/js/ravin.js`.
- دو مهاجرت تازه فقط برای `verbose_name`/پیش‌فرض (`core.0006`، `pluginhub.0004`)؛ جدول `ravin_cache`، فیلد `ravin_spec` و ثابت‌های امضا/رمزنگاری عمداً تغییر نکردند.
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
