# Changelog
قالب: [Keep a Changelog](https://keepachangelog.com/)، نسخه‌بندی [SemVer](https://semver.org/).

## [Unreleased]

## [0.10.0] - 2026-11-11

### CI and repository
- The browser job no longer fails or stalls when an apt mirror is down: system libraries and the Chromium download are separate steps with a short limit (4 minutes) and 3 tries each (`scripts/ci_install_browser.sh`); if apt stays unreachable the run goes on and only fails if Chromium really cannot start. Every step of the job has its own time limit. Pages use the fonts shipped in `static/fonts/`, not fonts from apt.
- GitHub Actions are pinned to full commit SHAs (Dependabot keeps them current).
- The Docker Compose docs and the compose file now warn that the default database password is for local trials only.

### Plugins
- Updating a plugin that adds a page, route or hook while the server still runs its previous code no longer fails with “… is not callable” (and no longer disables the plugin): the panel says to restart the server (`sudo systemctl restart ravix`), in all ten languages. A plugin without models simply loads the new code.
- A plugin's translations are registered as soon as the plugin is active and its files match the approved fingerprint, not only after its first page is opened, so the menu and page titles of a freshly started worker are in the panel's language instead of English.

### Windows
- Fix: `python manage.py runserver` failed on Windows with `ModuleNotFoundError: No module named 'fcntl'` (the theme installer imported it at start-up). All file locks (theme install, update executor, cron) now go through one cross-platform layer, `apps/core/filelock.py`: `fcntl.flock` on Linux and macOS (unchanged), `msvcrt.locking` on Windows; the platform modules are imported inside `try`, so nothing fails to load. The preflight no longer needs `os.geteuid` either.
- A new CI job on `windows-latest` installs the requirements, runs `check` and `migrate` on SQLite, starts the real `runserver` and asks `/healthz`, and runs the file-lock, accounts and update-source tests. A test also refuses any module-level import of a Linux-only module (`fcntl`, `pwd`, `grp`, …).

### Safe update — stage 5 (guide and release notes)
- «How to update Ravix» guide in the panel (Updates → How to update), in all ten languages: update from the panel, shared hosting (cron), your own server, what to do when an update fails or needs recovery, what an update never replaces, and what to check afterwards.
- The changelog now has a `[0.10.0]` section (the release workflow refuses a tag whose version has none).

### Safe update — stage 4 (real-server migration test)
- Fix found by the real-server job: the update executor could not open Gunicorn's socket (group `www-data`, mode 660) to health-check the site after a restart, so every update ended in "PermissionError" and a failed rollback. `ravix-update.service` now has `SupplementaryGroups=www-data` (only for that service); the probe names a permission problem clearly. A test keeps the executor's groups in line with the socket's group.
- Clear messages: a backup folder that is too small is reported before anything is written (needed vs. free space and the folder), a disk that fills up while writing says so and the half-written backup is removed, and a failed `migrate` shows the end of its output (the real reason) instead of its first lines. The migration job's job-status reader no longer breaks on multi-line messages.
- The stored job message now always keeps the final exception line (`SomeError: …`): secrets are masked and the text put on one line first, then the length cap removes the MIDDLE of a long traceback, never its end (`logs.compact`). Tests with a long Django-style traceback.
- Fix found on the real server (Ubuntu 24.04, Python 3.12): `requirements.hashes.lock` lacked `typing-extensions` (psycopg needs it on Python < 3.13), so `pip --require-hashes` refused the whole install. The lock now pins it (and `tzdata`, which Django needs on Windows) for every Python. New CI job "hash-lock" installs it in a clean venv on 3.11, 3.12 and 3.13.
- `install.sh` is transactional: the new Python environment is built and checked **first** (pip reads a copy of the lock the deploy user can open); if pip fails, `/srv/ravix/app` and the active virtualenv are untouched and the half-built `venv-<version>` is removed. The code is copied only after that, a copy of the previous code is saved first (`update_backups/code-before-install-*.tar.gz`), and the `venv` symlink is switched last, after `manage.py check` passes with the new code. If that check fails, the previous code is put back (the rejected code stays as `app.failed-*`). Running `install.sh` again over a half-finished install (leftover `venv-<version>`, `jobs`, `update_*`, `ravix-deploy`) rebuilds the environment from scratch.
- `ravix.env` is read literally, never run as shell code (`deploy/_envfile.sh`, `deploy/manage.sh`): `DEFAULT_FROM_EMAIL=Ravix <a@b.c>` without quotes no longer breaks `install.sh`, `backup.sh`, `restore.sh`. `install.sh` quotes values it writes, and both `install.sh` and `ravix_preflight` name the line and key of an unquoted value with spaces or `< > & ;` (never its value). Docs show the value quoted.
- The real-server job now installs with the system Python only (as on the owner's server), checks the three failed-install cases (wrong hash, missing dependency, new code that cannot start: server byte-identical, site healthy) and an install over leftovers, and seeds the unquoted `DEFAULT_FROM_EMAIL` line.
- The migration job checks `RAVIX_INSTALL_TOKEN` as designed (install.sh removes it once the site is installed), treats `auth_permission` / `django_content_type` as additive (old rows must stay identical, new models add rows), and lets `ravix_preflight` report only the HTTPS redirect of its own `--no-ssl` install.
- New CI job "مهاجرت سرور واقعی" (`scripts/e2e_server_migration.py`, ubuntu-24.04, real systemd): installs the release the production server runs with its own `install.sh`, seeds an owner-like site (content in four languages with translation groups, Studio, redirects, an MCP connection, SMTP settings, an external theme and plugin, the manual `/_plugin_files/` nginx block), runs the new `install.sh` over it and checks that nothing of the owner's changed (per-table snapshot, byte hashes, old `ravix.env` keys), then runs real updates through the real executor and the failure cases: broken migration, unhealthy `/healthz` after restart, full backup disk, tampered package, and `kill -9` of the executor (recovered by the timer).
- `scripts/e2e_updater.py`: the test migration is always built after the newest real one (no fixed number).
- `apps/updater/jobs.py`: no `chmod` on the wake-up file (bandit B103); the executor only needs the name.

### Safe update — stage 3 (live progress and dashboard)
- A live progress page for an update (`/panel/updates/job/<id>/`): six phases (download and verify, backup, replace files, database, switch, restart and health check), a percentage, and the outcome (done / rolled back / needs recovery). It polls `/panel/updates/job/<id>/status/`, which is exempt from maintenance mode, and shows "restarting" instead of an error while the site restarts. No inline script (CSP): `static/js/update-progress.js`.
- "Update now" button on the Updates page (needs `updates.manage` and a configured executor): notes in the admin's language, the compatibility pre-check, and a warning that lists files you changed or added in the program folders, which must be acknowledged with a checkbox before the update is queued. A queued update can be cancelled before it starts.
- Dashboard card "a new version is available" for users with `updates.view`, from the last known index (shared cache, no network on page load). The scheduled jobs refresh the index every ~6 hours (`RAVIX_UPDATE_AUTO_CHECK`, default on, off in tests).
### Added (به‌روزرسانی امن — مرحلهٔ ۲: اجراکنندهٔ جدا و محیط نسخه‌دار)
- **`UpdateJob` و اجراکننده:** وب فقط درخواست می‌دهد؛ `manage.py ravix_update_exec` (کاربر جدا `ravix-deploy` با systemd path unit، یا `ravix_cron` روی هاست) آن را اجرا می‌کند. `ravix_update queue|jobs|recover`.
- **محیط پایتون نسخه‌دار:** هنگام تغییر وابستگی‌ها `venv-<نسخه>` کنار قدیمی با `pip --require-hashes --only-binary=:all:` ساخته می‌شود؛ پس از `check` و `migrate` با کد تازه، symlink `venv` اتمیک جابه‌جا می‌شود؛ بازگشت همان جابه‌جایی است.
- **راه‌اندازی مجدد خودکار و `/healthz`:** پس از جابه‌جایی، سرویس دوباره راه‌اندازی و تا ۶۰ ثانیه سلامتش سنجیده می‌شود؛ ناسالم ⇒ بازگشت خودکار فایل‌ها، دیتابیس و محیط پایتون و راه‌اندازی دوبارهٔ نسخهٔ قبل. ریشه فقط یک دستور ثابت (`systemctl restart ravix`) را با فایل درخواست اجرا می‌کند.
- **بازیابی پس از مرگ پروسه:** ضربان کار و قفل؛ کار بی‌ضربان پس از ۲ دقیقه از پشتیبانِ تأییدشده بازیابی می‌شود (`recover_run`). حالت تعمیر (۵۰۳) با قفل مرده برنمی‌ماند.
- **هاست:** بستهٔ امضاشده می‌تواند `wheelhouse/` داشته باشد (`ravix_release --wheelhouse`)؛ نصب آفلاین با هش؛ بدون wheel مناسب ⇒ توقف پیش از هر تغییر.
- **`install.sh`:** کاربر `ravix-deploy`، مالکیت کد، `venv` به‌صورت symlink (مهاجرت نصب فعلی بدون نصب مجدد؛ پوشهٔ قدیمی می‌ماند)، واحدهای تازهٔ systemd و تنظیمات اجراکننده در env (فقط اگر نبودند).
- `ravix_preflight` وضعیت اجراکننده، صف و محیط نسخه‌دار را گزارش می‌دهد.
### Added (به‌روزرسانی امن — مرحلهٔ ۱: بستهٔ قابل‌راستی‌آزمایی)
- نسخهٔ هسته **۰٫۱۰٫۰**. بستهٔ هسته `manifest.json` (SHA-256 هر فایل) و `requirements.hashes.lock` دارد؛ اعتبارسنجی بسته فایل کم/اضافه/عوض‌شده و قفل ناسازگار را رد می‌کند. `scripts/make_hash_lock.py` قفل هش را از PyPI می‌سازد.
- یادداشت انتشار ده‌زبانه (`notes_i18n`) و کانال انتشار (`RAVIX_UPDATE_CHANNEL`) در `index.json`؛ `ravix_release --notes-i18n --channel`.
- **امضای آفلاین:** `ravix_release … --no-sign` و ابزار مستقل `scripts/ravix_sign.py` (فقط `cryptography`؛ روی سرور یا ویندوز با کلید روی فلش) و `--check` با کلید عمومی. آزمون خودکار تضمین می‌کند وب، اجراگر و افزونهٔ ناشر کلید خصوصی را نمی‌خوانند؛ `ravix_preflight` اگر کلید در محیط سایت باشد هشدار می‌دهد.
- `install.sh --serve-updates DIR`: nginx فایل‌های امضاشده را در `/updates/v1/` سرو می‌کند (بلاک امن، idempotent، با پشتیبان و بازگشت)؛ `RAVIX_UPDATES_PUBLISH_DIR` و چک `ravix_preflight`.
### Added (هویت سایت برای موتورهای جستجو — schema.org JSON-LD)
- کارت «هویت سایت برای موتورهای جستجو» در تنظیمات سایت: نوع (سازمان/شخص)، نام، نام‌های دیگر، لوگو از کتابخانهٔ رسانه، `sameAs` (فقط نشانی کامل `https://`؛ بقیه رد می‌شود) و توضیح کوتاه؛ برچسب‌ها ده‌زبانه (migration `core.0017`).
- JSON-LD در `<head>` (یک `@graph`): صفحهٔ اصلی هر زبان ← `WebSite` + `Organization`/`Person` (+ `SearchAction`)؛ نوشته ← `BlogPosting` (+ `workTranslation` از گروه ترجمه) + `BreadcrumbList`؛ برگه ← `WebPage` + `BreadcrumbList`. `@id`های ثابت: `<سایت>/#organization`، `<سایت>/#website`، `<نشانی>#article`. نوشتهٔ پیش‌نویس و `noindex` JSON-LD ندارد. خروجی با `json.dumps` و escape کردن `<`، `>`، `&`.
- هوک جدید افزونه `public.jsonld` (فیلتر) برای افزودن/تغییر گره‌ها (مثلاً `SoftwareApplication`)؛ خروجی افزونه پاک‌سازی می‌شود. مستندات: `docs/plugins.md`.
### Added (ترجمهٔ محتوا و سئو)
- **گروه ترجمه** برای نوشته و برگه (`translation_group`، از هر زبان یکی؛ قید در فرم و دیتابیس؛ migration `content.0010`) با کارت «ترجمه‌ها» در پنل (ساخت ترجمه، وصل‌کردن، جدا کردن)، `<link rel="alternate" hreflang>` با `x-default` (انگلیسی، وگرنه زبان پیش‌فرض)، `xhtml:link` در sitemap، انتخابگر زبانی که به ترجمه می‌رود و دستور `ravix_link_translations [--dry-run]`. در حالت چندسایتی خاموش است. مستندات: `docs/content-translations.md`.
- لینک **داخلی** در بدنهٔ Markdown دیگر `nofollow` نمی‌گیرد (بیرونی و دیدگاه‌ها بدون تغییر)؛ `content.0011` بدنه‌های ذخیره‌شده را دوباره می‌سازد.
### Added (Ravix MCP — مرحلهٔ M1: پایه)
- **سرور MCP** در `/mcp/` برای اتصال دستیارهای هوشمند (مثل Claude) به سایت، **پیش‌فرض خاموش**؛ روشن‌کردن فقط برای مدیرکل از «مدیریت سیستم ▸ دستیارهای هوشمند (MCP)» (`/panel/mcp/`). بدون session؛ هر دو نسل پروتکل (2026-07-28 و 2025-11-25/06-18/03-26).
- **سرور مجوز OAuth 2.1** داخلی: metadata (RFC 9728 و 8414)، ثبت کلاینت با CIMD و DCR (fetcher ضد SSRF با پین‌کردن IP)، فقط PKCE-S256، صفحهٔ رضایت ۱۰‌زبانه با هشدار قرمز برای آدرس‌های ناشناس و حالت «فقط hostهای مورد اعتماد»، توکن مبهم هش‌شده، refresh چرخشی با تشخیص استفادهٔ مجدد، revoke (RFC 7009).
- دسترسی هر اتصال = محدودهٔ داده‌شده ∩ آنچه مدیر برای آن نقش مجاز کرده ∩ توانایی‌های فعلی نقش کاربر؛ تغییر رمز یا غیرفعال‌شدن کاربر اتصال را می‌بندد.
- صفحهٔ «برنامه‌های متصل» برای کاربر (`/accounts/connected-apps/`) و برای مدیر؛ محدودیت نرخ، ثبت در گزارش ممیزی (`Action.MCP`) و پاک‌سازی خودکار در `ravix_cron`.
- اولین ابزار: `get_site_status` (فقط خواندنی). آزمایش e2e با SDK رسمی MCP (`scripts/e2e_mcp.py`)، مستندات `docs/mcp.md`.
### Security (Ravix Accounts ۱ — بخش ج: رمزهای عبور)
- ذخیرهٔ رمز با **Argon2id** (`argon2-cffi`، پارامترهای سازگار با هاست اشتراکی؛ `RAVIX_ARGON2_*`). رمزهای PBKDF2 قدیمی در ورود بعدی خودکار ارتقا می‌یابند؛ بدون بستهٔ argon2 به PBKDF2 برمی‌گردد و `ravix_preflight` اطلاع می‌دهد.
- سیاست رمز: حداقل طول قابل‌تنظیم (پیش‌فرض ۱۰ برای رمزهای جدید)، فهرست محلی رمزهای رایج (فارسی/فینگلیش/راویکس/نام سایت، بدون هیچ درخواست شبکه‌ای) و رد رمزهای ساخته‌شده روی نام سایت یا مشخصات خود شخص.
- **تغییر اجباری رمز**: مدیر می‌تواند برای کاربر `must_change_password` بگذارد؛ تا آن زمان فقط صفحهٔ رمز و خروج باز است (API → ۴۰۳). رمز جدید نباید با قبلی یکی باشد. migration `accounts.0008`.
### Security (دامنهٔ اعتماد کلیدهای امضا)
- کلیدهای مورد اعتماد حالا دامنه دارند: `plugin` (بستهٔ افزونه) و `core` (به‌روزرسانی هسته/قالب و فهرست آن). پیش‌تر یک فهرست مشترک بود و کلید ناشر افزونه‌ها می‌توانست هسته را امضا کند. env: `برچسب:دامنه:کلید` (شکل قدیمی هر دو دامنه را می‌دهد و `ravix_preflight` هشدار می‌دهد)؛ `TrustedKey.scopes` (migration `plugins.0004`؛ ردیف‌های موجود هر دو دامنه را نگه می‌دارند) و ویرایش آن در پنل.
- دو کلید عمومی رسمی به‌روزرسانی هسته (اصلی + یدک، فقط دامنهٔ `core`) در `apps/core/official.py`؛ `verify_release.py` و `build_cpanel_zip.py` نبودشان را در بسته خطا می‌گیرند.
### Added (نصب روی هاست اشتراکی cPanel)
- حالت هاستینگ (`config.settings.hosting`، فعال با `ravix-hosting.marker` یا `RAVIX_HOSTING=shared`)؛ مسیر VPS (`install.sh`) بدون تغییر. `passenger_wsgi.py` سازگار با Passenger و LiteSpeed، نصب در زیرپوشه (`RAVIX_SCRIPT_NAME`)، پایتون حداقل ۳٫۱۱ با پیام روشن.
- پشتیبانی کامل MySQL/MariaDB با PyMySQL (utf8mb4؛ یکتایی‌های شرطی با ستون مجازی در `shop.0010` و `sites.0004`؛ پشتیبان/بازیابی MySQL) و جاب CI تازه.
- نصب‌کنندهٔ وب ده‌زبانه با کد نصب یک‌بارمصرف (فایل ۶۰۰ بیرون از `public_html`، مقایسهٔ زمان‌ثابت، محدودیت تلاش)، بررسی میزبان، فرم پایگاه‌داده، قفل دائمی، فایل env با دسترسی ۶۰۰.
- `manage.py ravix_cron` (هر ۵ دقیقه): انتشار زمان‌بندی‌شده، پاک‌سازی، صف ایمیل، پشتیبان روزانه، keep-alive؛ با قفل ضدهم‌پوشانی. صفحهٔ سلامت و نمایش آخرین اجرا.
- کش صفحهٔ مهمان (پیش‌فرض: روشن روی هاستینگ، خاموش روی VPS؛ تنظیمات ▸ سرعت)، SMTP از پنل با ایمیل آزمایشی و صف تلاش دوباره، به‌روزرسانی با آپلود ZIP، `ravix-<نسخه>-cpanel.zip`، `.htaccess` امن، مستندات (fa/en/۸ زبان).
- جاب CI `shared-hosting` (کاربر غیر root، MariaDB، پایتون ۳٫۱۱، بدون Redis و systemd) و ماتریس پایتون ۳٫۱۱–۳٫۱۳.
### Fixed
- به‌روزرسانی هسته پوشهٔ `locale` را هم جایگزین می‌کند (پیش‌تر از فهرست ورودی‌ها جا افتاده بود).
- نصب در زیرپوشه: دروازهٔ نصب، حالت نگهداری، ۴۰۴ و ریدایرکت‌ها به‌جای `request.path` از `path_info` استفاده می‌کنند.
### Added (Ravix Studio ۱: صفحه‌ساز حرفه‌ای)
- `apps/studio`: ویرایشگر تمام‌صفحه با پیش‌نمایش زنده، کشیدن و رها کردن (SortableJS)، Navigator، واگرد/ازنو، ذخیرهٔ خودکار، صفحه‌کلید کامل و تنظیمات جدای دسکتاپ/تبلت/موبایل؛ برای صفحه‌ها **و نوشته‌ها**. جایگزین `apps/builder` (migration `studio.0002` برگشت‌پذیر، ریدایرکت مسیرهای قدیمی).
- ویجت‌ها: بخش، ستون، بخش داخلی، فاصله، عنوان، متن، تصویر، دکمه، نماد، جداکننده، ویدیو (YouTube/Vimeo/آپارات/MP4، بارگذاری پس از کلیک)، فهرست نماد، کارت، گالری، نقل‌قول، HTML سفارشی (فقط مدیر). API ویجت افزونه‌ها: `studio_widgets` در `plugin.json` و `register_studio_widget`.
- رندر یک‌بار هنگام انتشار (`RENDERER_VERSION`، فرمان `studio_rebuild`)، CSS هش‌دار برای حجم‌های بزرگ، گردش تأیید (پیش‌نویس ← بررسی ← تأیید/رد ← انتشار)، قفل خوش‌بینانه و هشدار ویرایش هم‌زمان، سقف نسخه‌ها (`RAVIX_STUDIO_MAX_VERSIONS`)، درون‌ریزی/برون‌ریزی JSON با schema، `supports.studio_full_width` در قالب‌ها، قفل سرور `RAVIX_STUDIO_ENABLED`.
- ۱۰ زبان، آزمون‌های امنیتی (XSS، تزریق CSS)، حالت CI «بدون Studio».
### Added (افزونه‌ها: صفحهٔ سایت، پوشهٔ خصوصی، دانلود فایل)
- `site_routes` در `plugin.json`: صفحه‌ای که افزونه زیر پیشوند زبان سایت (`/<زبان>/مسیر/`) یا بدون پیشوند (`/مسیر/`، مثلاً لینک ثابت دانلود) سرو می‌کند؛ اولین بخش مسیر نمی‌تواند از بخش‌های رزروشدهٔ هسته باشد و فقط مسیرهای اعلام‌شدهٔ افزونه‌های فعال تطبیق می‌خورند.
- `api.render_public(..., index=True)`: صفحهٔ عمومی افزونه می‌تواند قابل ایندکس باشد (پیش‌فرض همچنان noindex).
- `api.data_dir()` (پوشهٔ خصوصی هر افزونه، خارج از media، `RAVIX_PLUGIN_DATA_DIR`) و `api.file_response()` (دانلود با `Content-Disposition: attachment` و `nosniff`؛ با `RAVIX_ACCEL_REDIRECT_PREFIX` فایل را nginx با `X-Accel-Redirect` می‌فرستد و پردازهٔ Gunicorn درگیر نمی‌شود).
- `install.sh`/nginx: پوشهٔ `plugin_data`، مکان `internal` برای `/_plugin_files/`، سقف آپلود در صفحه‌های مدیریتی افزونه‌ها؛ پشتیبان‌گیری و بازیابی و بررسی `ravix_preflight` برای پوشهٔ جدید.
### Added (sitemap افزونه‌ها)
- hook عمومی `public.sitemap`: افزونهٔ فعال می‌تواند صفحه‌های عمومی خود را (با `lastmod` و hreflang اختیاری) به sitemap هر زبان اضافه کند؛ خروجی پاک‌سازی می‌شود (فقط مسیر محلی همان زبان، بدون query/تکرار، سقف ۱۰۰۰) و خطای افزونه sitemap را نمی‌شکند. خانه، وبلاگ و دسته‌ها از قبل hreflang همهٔ زبان‌ها و x-default داشتند (تست افزوده شد).
### Added (منو به‌ازای هر زبان)
- هر آیتم منو می‌تواند «زبان‌های دیگر» داشته باشد (`translations`: عنوان و مقصد جدا برای هر زبان؛ برگه/دسته با نامک یا پیوند) با برگشت میدان‌به‌میدان به مقدار خود آیتم (زبان پیش‌فرض). بدون migration دیتابیس (سند JSON قالب)، آیتم‌های قدیمی بدون تغییر معتبرند؛ قالب‌ها همان `menus.header` / `menus.footer` را با عنوان و نشانی زبان بازدیدکننده می‌گیرند.
### Added (زبان نوشته و برگه)
- فیلد «زبان» برای نوشته و برگه (migration `content.0009` برگشت‌پذیر؛ موجودها به زبان پیش‌فرض سایت)، انتخابگر در ویرایشگر و فیلتر/ستون زبان در فهرست پنل.
- گزینهٔ «هر نوشته و برگه فقط به زبان خودش نمایش داده شود» (`core.0014`؛ سایت‌های موجود خاموش می‌مانند): فهرست‌ها، دسته، برچسب، جستجو، فید و sitemap هر زبان فقط محتوای همان زبان، ۳۰۱ به پیشوند درست، `lang`/`dir`/`canonical` از زبان نوشته، و صفحهٔ «هنوز مقاله‌ای به این زبان نیست» با لینک به زبان‌های دارای مقاله.
- متغیر قالب `languages` (فهرست زبان‌ها با نشانی همان صفحه یا نزدیک‌ترین صفحه در هر زبان) برای انتخابگر زبان قالب‌ها.
### Fixed
- هشدار اول کش خراب روی ماشین تازه‌بوت‌شده (ساعت `monotonic` از بوت شروع می‌شود) خاموش می‌ماند و ۴ تست CI را می‌شکست.
### Added (Ravix Shop ۲: کاتالوگ)
- محصول متغیر با ویژگی‌ها و تنوع (قیمت، موجودی، SKU، وزن و تصویر جدا)، ساخت خودکار تنوع‌ها، SKU یکتا، وزن، برچسب، دسته‌بندی تو در تو، محصولات مرتبط و بازهٔ قیمت؛ سبد و سفارش با تنوع (snapshot) و رزرو اتمی موجودی هر تنوع.
- هشدار کمبود موجودی (ایمیل به زبان سایت + hook `shop.low_stock`) و فیلتر/نشان آن در فهرست محصولات؛ API با `sku`, `weight_grams`, `tags`, `variants`, `price_max`, `related` و فیلتر `tag`.
- migration `shop.0009` برگشت‌پذیر و بی‌خطر برای سایت‌های موجود (با تست).
### Added (افزونهٔ داخلی: Ravix Shop از پنل فعال/غیرفعال می‌شود)
- الگوی عمومی «افزونهٔ داخلی» (`apps/core/modules.py`، `builtin_modules.py`، `ModuleState`): ثبت ماژول، سوییچ در دیتابیس، قفل سرور، راهنمای راه‌اندازی، و پنهان‌سازی منو، مسیر، API، hook و هشدار (ماژول غیرفعال ۴۰۴ می‌دهد و هیچ داده‌ای حذف نمی‌شود).
- افزونه‌ها ← «افزونه‌های داخلی»: کارت Ravix Shop با آیکون، توضیح، نسخه و دکمهٔ فعال/غیرفعال (فقط مدیرکل، قابلیت `modules.manage`، با لاگ ممیزی)؛ `RAVIX_SHOP_ENABLED=false` فقط قفل سرور است.
- راهنمای راه‌اندازی فروشگاه (ارز، اطلاعات فروشگاه، درگاه، ارسال، اولین محصول)؛ فیلدهای اطلاعات فروشگاه در `ShopSettings`.
- migration نصب‌های موجود: `shop.0008` فروشگاه را برای سایتی که کاربر یا داده دارد روشن نگه می‌دارد.
### Added (Ravix Shop ۱: پایه)
- نام بیرونی «Ravix Shop»؛ مدل `ShopSettings` (ارز هر سایت، نمایش به تومان، آستانهٔ کمبود موجودی، ایمیل به مشتری) با migration `0006` که سایت‌های موجود را با همان ارز قبلی نگه می‌دارد و برگشت‌پذیر است؛ صفحهٔ «تنظیمات فروشگاه» در پنل.
- چندارزی: ریال، تومان، دلار، یورو با ذخیرهٔ عدد صحیح در واحد پایه و قالب‌بندی درست هر زبان (`apps/shop/money.py`، برچسب `{% price %}`)؛ فرم قیمت محصول به واحد نمایشی؛ `currency` در API؛ تست محافظ اشتباه ۱۰ برابری ریال/تومان.
- ایمیل وضعیت سفارش به زبان مشتری و hook `shop.order_status_changed` برای پیامک.
### Changed (کارایی nginx و Gunicorn، رفتار install.sh)
- قالب nginx (`deploy/nginx-ravix.conf`): gzip با `Vary` و فهرست نوع‌های کامل‌تر (تصویر/فونت فشرده نمی‌شوند)، keepalive به مرورگر و به Gunicorn (`upstream keepalive`، `proxy_http_version 1.1`)، `/static/` با نام هش‌دار `max-age=31536000, immutable` و نام بدون هش یک ساعت (نگاشت `$ravix_static_cache`)، `/media/` سی‌روزه.
- `install.sh` بعد از certbot **HTTP/2** را روشن می‌کند (`http2 on;` برای nginx ≥ 1.25.1، وگرنه پارامتر `http2`؛ با نسخهٔ پشتیبان و بازگرداندن در صورت رد شدن توسط `nginx -t`).
- Gunicorn: تعداد worker از CPU و رم (و سقف cgroup) محاسبه می‌شود؛ سرور ۲ گیگ ← ۳ تا ۵ worker؛ `GUNICORN_WORKERS` / `WEB_CONCURRENCY` همچنان جایگزین می‌کنند. `keepalive` از ۵ به ۶۵ ثانیه.
- `install.sh` برای سایتی که از قبل نصب شده «گام آخر» و توکن نصب را چاپ نمی‌کند، توکن جدید نمی‌سازد و توکن باقی‌مانده را از `ravix.env` پاک می‌کند. آزمون: `apps/core/tests/test_install_script_logic.py`.
### Added (لوگوی رسمی Ravix)
- بستهٔ لوگو در `static/brand/` (SVG روشن/تیره، آیکون، wordmark، `favicon.ico`، PNGها و `apple-touch-icon`)؛ رنگ‌های برند به‌صورت متغیرهای CSS پنل: `--brand-navy` (#0F172A)، `--brand-indigo` (#4F46E5، همان `--c-primary`) و `--brand-amber` (#F59E0B).
- منوی کناری پنل: آیکون `ravix-icon.svg` به‌جای مربع بنفش با «ر» (لوگوی بارگذاری‌شدهٔ خود سایت همچنان اولویت دارد؛ پیوند «مشاهدهٔ سایت» همان است). صفحهٔ ورود، بازیابی رمز و نصب‌کننده: لوگوی کامل. صفحهٔ خطای سایت و صفحهٔ مستقل خطا/حالت تعمیر: لوگوی کامل (صفحهٔ مستقل با دو نسخه به‌صورت درون‌خطی و نسخهٔ تیره در حالت تیره).
- favicon و `apple-touch-icon` پیش‌فرض برای پنل و سایت عمومی، فقط وقتی سایت favicon اختصاصی ندارد.
- ایمیل‌های سیستم (تأیید ایمیل، بازیابی رمز، آزمایشی، اعلان دیدگاه): متن ساده همان می‌ماند و یک نسخهٔ HTML با لوگوی PNG جاسازی‌شده (`cid:`) هم فرستاده می‌شود؛ اگر ساخت HTML شکست بخورد فقط متن ساده می‌رود (`apps/core/mailhtml.py`).
- README مخزن: لوگو بالای صفحه با `<picture>` برای حالت تیره و روشن GitHub. آزمون: `apps/core/tests/test_brand.py`.
### Fixed
- صفحهٔ ورود نام سایت، لوگو و favicon سایت را نشان نمی‌داد، چون `LoginView` جنگو متغیر `site` را با `RequestSite` عوض می‌کرد.
### Added (پیوند «مشاهدهٔ سایت» در پنل)
- لوگو و نام سایت بالای منوی کناری پنل (و منوی موبایل) به صفحهٔ اصلی سایت عمومی در زبان فعلی کاربر می‌روند (همان تب)، با عنوان/متن دسترس‌پذیر «مشاهدهٔ سایت»؛ آیکون کوچک کنارش همان صفحه را در تب جدید (`rel=noopener`) باز می‌کند. ترجمه برای هر ۱۰ زبان؛ آزمون `apps/dashboard/tests/test_panel_site_link.py`.
### Changed
- ایمیل‌های رسمی: گزارش امنیتی `security@ravixcms.com` و پشتیبانی `support@ravixcms.com` (SECURITY.md، README، CONTRIBUTING، سرصفحهٔ کاتالوگ‌های ترجمه، `.well-known/security.txt` پروژه) به‌جای نشانی Gmail.
### Added (دسترسی پوشه‌های کد و زبان install.sh)
- `ravix_preflight` و «سلامت سایت» (بررسی «پوشه‌های کد») پوشه‌های افزونه و قالب را با **گروه‌های واقعی همان فرایند** می‌خوانند و فهرست می‌کنند (نه فقط «قابل نوشتن نیست»)، و در صورت شکست مالک/مجوز پوشه، کاربر و گروه‌های فرایند و راه‌حل (`SupplementaryGroups=ravix`) را می‌نویسند (`apps/core/fsaccess.py`).
- آزمون `test_deploy_permissions`: گروه‌های هر unit با مالکیت و مجوزهایی که `install.sh` می‌گذارد هماهنگ باشند؛ هر unitی که با `Group=` غیر از `ravix` اجرا شود باید `SupplementaryGroups=ravix` داشته باشد.
- `install.sh` انگلیسی است و با `RAVIX_CLI_LANG=fa` (یا `--lang fa`) فارسی اجرا می‌شود؛ ترجمه در `deploy/install.fa.tsv` (برای زبان دیگر فایل `deploy/install.<lang>.tsv` بگذارید)؛ آزمون `test_install_script_i18n` کامل‌بودن و جانگهدارها را می‌سنجد.
### Added (صفحهٔ اصلی و ترجمهٔ قالب/افزونه)
- **hero با چند دکمه** (تا ۳): `Section.buttons`؛ `button_label`/`button_url` همان دکمهٔ نخست می‌ماند (سازگاری با قالب‌های قدیمی).
- **بخش «ویژگی‌ها»** (`features`): برای هر مورد آیکون (از فهرست ثابت)، عنوان و متن کوتاه؛ تگ `{% feature_icon %}` برای کشیدن آیکون. قالب با `supports.home_sections` آن را اعلام می‌کند.
- **بخش «متن» و بدنهٔ hero Markdown می‌پذیرند** (پاک‌سازی‌شده با همان فهرست تگ‌های نوشته‌ها)؛ متن ساده قدیمی بدون تغییر می‌ماند (خط جدید = شکست خط).
- **متن‌های صفحهٔ اصلی برای هر زبان**: در «سفارشی‌سازی ← صفحهٔ اصلی» زبانه برای هر زبان؛ عنوان، زیرعنوان، بدنه، متن دکمه‌ها و موردهای ویژگی‌ها به‌ازای زبان؛ ساختار (نوع، پیوند، آیکون، ترتیب) مشترک؛ خالی = متن زبان پیش‌فرض سایت.
- **آزمون ترجمه**: متن هسته فقط از کاتالوگ هسته؛ قالب و افزونه یا متن هسته را دوباره به کار می‌برند یا locale خودشان را برای هر ۹ زبان کامل می‌دهند.
- قالب پیش‌فرض ۱.۲.۰: نمایش چند دکمه و بخش ویژگی‌ها. قالب جدید **Nova** ۱.۱.۰ (`themes/nova`).
### Added (چندزبانگی؛ ۱۰ زبان رابط: fa، en، ar، tr، es، fr، de، ru، pt-br، zh-hans)
- **همهٔ متن‌های رابط** (پنل، قالب پیش‌فرض، ایمیل‌ها، پیام‌های خطا و اعتبارسنجی، نصب‌کننده، افزونه‌های همراه، جاوااسکریپت) با gettext علامت‌گذاری شد؛ msgidها انگلیسی‌اند و کاتالوگ `fa` ترجمهٔ فارسی قبلی است. فقط رابط ترجمه می‌شود، نه محتوای کاربر. کاتالوگ‌ها در `locale/` (و `.mo`ها در گیت).
- **جهت خودکار**: `rtl` برای `fa`/`ar` و `ltr` برای بقیه؛ CSS پنل و قالب با ویژگی‌های منطقی (`inline-start/end`) در هر دو جهت کار می‌کند.
- **تقویم و رقم**: جلالی و رقم فارسی فقط برای `fa`؛ بقیه میلادی و رقم لاتین (`apps/core/localefmt.py`). فونت به‌ازای خط: Vazirmatn (فارسی/عربی)، Inter (لاتین/سیریلیک)، Inter + فونت سیستم (چینی).
- **انتخاب زبان**: گزینشگر در پاورقی سایت و در پنل/صفحات ورود؛ ذخیره در کوکی `ravix_lang` و نمایهٔ کاربر (`User.language`)؛ «زبان پیش‌فرض سایت» در تنظیمات سایت. ترتیب: پیشوند آدرس ← کوکی ← نمایه ← پیش‌فرض سایت ← فارسی.
- **پیشوند زبان در آدرس سایت عمومی و فروشگاه** (`/fa/…`، `/en/…`، `/de/…`) با `hreflang` (و `x-default`) و canonical دارای پیشوند؛ `/sitemap.xml` فهرست sitemap هر زبان (`/<lang>/sitemap.xml` با `xhtml:link`) است و `robots.txt` در ریشه می‌ماند.
- ترجمهٔ قالب (`locale/<lang>.json`، کامپایل‌شده توسط Ravix) و افزونه (`locale/<lang>/LC_MESSAGES/django.mo`) با `apps/core/catalogs.py`؛ ترجمهٔ هسته همیشه اولویت دارد. راهنما: `docs/translation.md`.
- آزمون `apps.core.tests.test_i18n_completeness`: شکست برای متن علامت‌نخورده (فارسیِ پایتون، متن/ویژگی‌های دیدنی قالب‌ها)، رشتهٔ علامت‌خورده‌ای که در کاتالوگ نیست، ترجمهٔ خالی/fuzzy/منسوخ، ناهماهنگی رشته‌ها بین زبان‌ها، جانگهدار جاافتاده و `.mo` کهنه.
- **متن‌های سایت به‌ازای هر زبان**: نام، شعار، توضیح و متن پاورقی در «تنظیمات سایت» برای هر زبان قابل تنظیم‌اند (`SiteSettings.translations`)؛ فیلدهای ساده متن زبان پیش‌فرض و جایگزین زبان‌های خالی‌اند. نصب‌کننده (و `ravix_install`) نام «Ravix» را به زبان انتخاب‌شده پیشنهاد می‌دهد.
- `name` و `description` در `theme.json` و `plugin.json` اختیاراً چندزبانه (`{"fa": "…", "en": "…"}`)؛ نمایش به زبان فعال با بازگشت به متن پیش‌فرض (`en`). قالب پیش‌فرض و سه افزونهٔ همراه برای هر ۱۰ زبان نام و توضیح دارند (نسخه‌ها ۱.۰.۱ / ۱.۱.۱ شد، چون اثر انگشت افزونه با تغییر فایل عوض می‌شود).
- ارتقا: برگه‌ها و نوشته‌هایی که نامکشان کد زبان است خودکار تغییر نام می‌دهند (مثلاً `de-page`) با ریدایرکت ۳۰۱ و ثبت در لاگ ممیزی (migration، `ravix_fix_slugs`، سلامت سایت و preflight).
- **پیام‌های خطاب به دیگران به زبان گیرنده‌اند، نه زبان CLI**: ایمیل تأیید و بازنشانی گذرواژه (زبان نمایه ← زبان رابط فعلی ← زبان سایت)، اطلاع‌رسانی دیدگاه به مدیر، ایمیل افزونهٔ contact_form (نسخه ۱.۰.۲؛ `api.recipient_language` / `api.use_language`)، گزارش‌های `publish_scheduled` و اصلاح نامک، و تاریخچهٔ سفارش (لغو خودکار، استرداد، پرداخت) به زبان سفارش. فیلد جدید `Order.language` (migration `shop.0005`؛ سفارش‌های قدیمی از نمایهٔ مشتری یا زبان سایت می‌خوانند). `send_test_email` گزینهٔ `--lang` دارد. ترجمهٔ فارسیِ اشتباه (fuzzy) پیام «نوشته طبق زمان‌بندی منتشر شد» اصلاح شد.
- **آدرس‌های رسمی پروژه**: سایت `https://ravixcms.com`، مستندات `https://ravixcms.com/docs`، مخزن `https://github.com/ravixcms/ravixcms`، منبع به‌روزرسانی `https://updates.ravixcms.com` و مخزن افزونه/قالب `https://plugins.ravixcms.com` در `apps/core/official.py`؛ همه با `RAVIX_WEBSITE_URL`، `RAVIX_DOCS_URL`، `RAVIX_REPO_URL`، `RAVIX_UPDATE_URL` و `RAVIX_OFFICIAL_REGISTRY_URL` قابل تغییرند. کلید عمومی ناشر هنوز خالی است: **تا وقتی هیچ کلید مورد اعتمادی تنظیم نشده، منبع رسمی به‌روزرسانی و مخزن افزونه غیرفعال‌اند** (شبکه صدا زده نمی‌شود؛ پنل به‌روزرسانی، «سلامت سایت» و `ravix_update check` پیام روشن می‌دهند، نه خطا).
- پاورقی پنل نسخه، وب‌سایت و مستندات را نشان می‌دهد؛ صفحهٔ نصب‌کننده پیوند راهنما دارد؛ User-Agent درخواست‌های به‌روزرسانی/مخزن `Ravix/<نسخه> (+https://ravixcms.com)` است؛ `homepage` در `theme.json` (تازه) و `plugin.json` ثبت شد؛ README، SECURITY، CONTRIBUTING و docs به‌روز شدند (نسخهٔ قالب پیش‌فرض ۱.۱.۲ و افزونه‌ها ۱.۰.۳/۱.۰.۲/۱.۰.۲).
### Fixed
- **آزمون ناپایدار** `ListQueryTests` فروشگاه («۸ != ۷») و دیگر آزمون‌های شمارش پرس‌وجو: کش یک‌ثانیه‌ای حالت تعمیر (`apps/updater/middleware.py`) وسط اندازه‌گیری منقضی می‌شد و یک `SELECT` اضافه می‌داد؛ اجراگر آزمون حالا TTL بلند و پاسخ تازه در شروع هر آزمون می‌دهد (`config/testrunner.py`) و آزمون تثبیت دارد. این همان شکست پنهان زیر خطای «cannot pickle 'traceback' object» در اجرای موازی بود (برای دیدن شکست واقعی `pip install tblib`؛ CONTRIBUTING).
- **۵۰۰ در `/panel/themes/` پس از آپلود قالب (`Missing staticfiles manifest entry for 'themes/<slug>/screenshot.png'`).** استاتیک قالب نصب‌شده از پنل حالا از `/theme-assets/<slug>/…` (فقط‌خواندنی، بدون manifest، بدون `collectstatic` و restart) سرو می‌شود؛ و storage تولید (`apps.core.staticstorage.RavixStaticFilesStorage`) برای ورودی ناموجود در manifest به‌جای ۵۰۰ نشانی ساده می‌دهد. نامک تکراری (قالب همراه پروژه + نسخهٔ آپلودی): پوشهٔ مقدم برنده است و پنل/preflight پوشهٔ بی‌استفاده را نشان می‌دهند. آزمون با storage واقعی تولید برای نصب/ارتقا/حذف قالب و افزونه. راهنما: `docs/themes.md`.
- **۵۰۰ در `/panel/plugins/` روی سرور واقعی (`PermissionError: /srv/ravix/app/plugins`).** `install.sh` کد را `root:ravix` با مجوز 750 می‌گذارد، ولی `ravix.service` با `Group=www-data` اجرا می‌شد و فرایند عضو گروه `ravix` نبود. حالا `SupplementaryGroups=ravix` در `ravix.service`، `ravix-backup.service`، `ravix-publish-scheduled.service` و `ravix-expire-orders.service` هست (روی سرور دارای نصب قبلی: فایل‌های unit را دوباره کپی کنید، `systemctl daemon-reload` و `systemctl restart ravix`، یا `install.sh` را دوباره اجرا کنید؛ `drop-in` دستی دیگر لازم نیست).
- پوشهٔ افزونه‌ها/قالب‌ها که فرایند نتواند بخواند دیگر ۵۰۰ نمی‌دهد: لیست افزونه‌ها هشدار با نام پوشه و راه‌حل نشان می‌دهد، کشف قالب‌ها فقط لاگ می‌کند.
- ورودی‌های فایل در پنل دکمه و متن «فایلی انتخاب نشده» ترجمه‌شدهٔ خود Ravix را دارند (به‌جای متن انگلیسی مرورگر)؛ ورودی واقعی شفاف و روی ناحیه است، پس با صفحه‌کلید در دسترس است و کشیدن‌ورهاکردن هم کار می‌کند (`static/js/ravix.js`).
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
- `LICENSE`: دارندهٔ حق نشر «The Ravix Authors» (MIT بدون تغییر). `SECURITY.md`: راه تماس security@ravixcms.com، گزارش خصوصی، پاسخ اولیه ۷ روز، نسخه‌های پشتیبانی‌شده.
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
