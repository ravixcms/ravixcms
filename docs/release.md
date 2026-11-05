# CI و فرایند انتشار نسخه‌دار (GitHub)

> **وضعیت راستی‌آزمایی:** گردش‌کارها روی GitHub واقعی اجرا **نشده‌اند**. با `actionlint` سنجیده شدند و مرحله‌های اصلی همان دستورها محلی اجرا شدند (آزمون روی Python 3.11/3.12/3.13 با SQLite و روی PostgreSQL 16 با Python 3.13، `e2e_backup_restore` و `e2e_updater` روی PG16، `pip-audit`، `bandit`، `check --deploy`، ساخت و `verify_release.py` بستهٔ zip). اجرا نشده: ساخت/push image داکر، کش `gha`، Release واقعی، و ساخت Python 3.12 + PostgreSQL در خودِ runner. **اولین اجرا را روی تگ آزمایشی (`v0.0.0-test`… که با نسخهٔ کد نمی‌خواند و در preflight رد می‌شود؛ پس از bump موقت نسخه، `v<نسخه>-rc1`) امتحان کنید.**

## CI (`.github/workflows/ci.yml`)
اجرا با push به `main`، هر pull request، هفتگی (دوشنبه) و دستی؛ `release.yml` همین فایل را با `workflow_call` فرا می‌خواند.

| Job | چه می‌کند |
|---|---|
| `migrations` | `makemigrations --check --dry-run` (migration جاافتاده ⇒ شکست) و `manage.py check` |
| `test` | ماتریس **Python 3.12 / 3.13 × SQLite / PostgreSQL 16** (service container)، به‌علاوهٔ یک اجرا با `RAVIX_SHOP_ENABLED=false`. آزمون‌ها سریال اجرا می‌شوند (پایگاه‌داده و کش مشترک‌اند) |
| `e2e-postgres` | `scripts/e2e_backup_restore.py` و `scripts/e2e_updater.py` روی PG16 با `pg_dump/pg_restore` هم‌نسخه |
| `security` | `pip-audit -r requirements.lock --no-deps --strict`؛ `bandit` با baseline؛ `check --deploy --fail-level WARNING` |
| `docker-build` | ساخت image (بدون push)، اجرای `manage.py check` درونش، و بررسی غیر root بودن |

### bandit و baseline
`scripts/bandit_check.sh` فقط یافتهٔ **تازه** با شدت Medium به بالا را رد می‌کند؛ یافته‌های شناخته‌شده (۵۶ مورد: بیشترشان `mark_safe` در تگ‌های آیکن/نشانه‌گذاری، ثابت‌های SQL با نام جدول `quote_name`‌شده، `try/except/pass`) در `.bandit-baseline.json` ثبت‌اند. `tarfile.extractall` در `apps/updater/backup.py` پس از اعتبارسنجی مسیر/لینک/دستگاه است و با `# nosec B202` علامت خورده. پس از **بازبینی دستی** یک یافتهٔ جدید: `scripts/bandit_check.sh --update` و commit کردن baseline. baseline را بی‌بازبینی به‌روز نکنید.

### pip-audit
اگر شکست خورد، نسخهٔ پین‌شده در `requirements.lock` آسیب‌پذیری شناخته‌شده دارد؛ آن را ارتقا دهید (Dependabot هفتگی PR می‌دهد). اجرای هفتگی برای همین است که بدون commit هم خبر برسد.

## انتشار (`.github/workflows/release.yml`)
1. `apps/core/version.py` → `RAVIX_VERSION` را بالا ببرید و در `CHANGELOG.md` بخش `## [X.Y.Z]` بنویسید.
2. commit را به `main` ببرید و CI سبز شود.
3. تگ: `git tag -a vX.Y.Z -m "Ravix X.Y.Z" && git push origin vX.Y.Z` (برای پیش‌انتشار `vX.Y.Z-rc1`).
4. خودکار: **preflight** (تگ = `RAVIX_VERSION`، بخش CHANGELOG موجود، commit در تاریخچهٔ `main`) ← **ci** (همهٔ بالا) ← موازی: **package** (`ravix-X.Y.Z.zip` با `TZ=UTC` + `SHA256SUMS` + `verify_release.py`) و **docker** (image روی `ghcr.io/ravixcms/ravixcms` با برچسب `X.Y.Z`، `X.Y` و `latest`؛ برای نسخهٔ `-rc/-beta` فقط برچسب خودش؛ provenance و SBOM روشن) ← **release** (GitHub Release با zip و `SHA256SUMS`؛ تگ دارای `-` به‌صورت pre-release).
5. مجوز: `contents: write` فقط در job نهایی Release و `packages: write` فقط در job داکر؛ بقیه فقط‌خواندنی.

## امضا دستی و آفلاین است (عمداً در CI نیست)
کلید خصوصی امضای ناشر **هرگز** در GitHub Secrets، runner یا مخزن نمی‌رود: نفوذ به یک workflow یا وابستگی آلوده نباید بتواند بستهٔ امضاشدهٔ مخرب بسازد. بنابراین Release خودکار فقط **checksum** دارد (یکپارچگی دانلود)، نه امضای ناشر. برای بستهٔ امضاشدهٔ به‌روزرسانی هسته:
1. روی ماشین امن و ترجیحاً آفلاین، همان تگ را checkout کنید.
2. `python manage.py ravix_release core --out dist/ --key-file <فایل seed کلید خصوصی، مجوز 600>` (یا `RAVIX_RELEASE_SIGNING_KEY` در همان پروسه). قالب‌ها: `ravix_release theme`؛ ساخت کلید: `ravix_release keygen`.
3. `sha256sum` خروجی را با zip و `SHA256SUMS` منتشرشده مقایسه کنید و فایل‌های امضاشده را **دستی** به همان Release پیوست کنید (`gh release upload vX.Y.Z dist/<فایل>`).
4. کلید عمومی را جداگانه و از کانال معتمد پخش کنید؛ سایت‌ها بسته‌های بدون امضای معتبر را رد می‌کنند.
کلید را پس‌انداز آفلاین (مثلاً دو نسخه روی رسانهٔ رمزشده) نگه دارید؛ گم‌شدنش یعنی چرخش کلید در همهٔ سایت‌ها.

## راه‌اندازی سرور رسمی به‌روزرسانی (راهنمای کامل ناشر)

هدف: یک میزبان HTTPS که فایل‌های امضاشدهٔ هسته/قالب را سرو کند و همهٔ سایت‌های Ravix فقط بسته‌ای را بپذیرند که با **کلید خصوصی آفلاین شما** امضا شده است. سایت‌ها به خود سرور اعتماد ندارند؛ فقط به کلید عمومی و SHA-256: سرور لو برود یا دست‌کاری شود، بدون کلید خصوصی نمی‌تواند بستهٔ پذیرفتنی بسازد.

```
 ماشین آفلاین/امن شما                     سرور استاتیک HTTPS                   سایت‌های مشتری
 ───────────────────────                 ──────────────────                   ───────────────
 کلید خصوصی (هرگز بیرون نمی‌رود)  ──►     /updates/v1/index.json     ──►        بررسی SHA-256 + امضا
 ravix_release core …  (dist/)    ──►     /updates/v1/index.json.sig            با کلید عمومیِ داخل کد
                                          /updates/v1/ravix-core-X.zip           (apps/core/official.py)
```

### ۱. ساخت کلید آفلاین (یک‌بار)
روی ماشینی که به اینترنت وصل نیست یا حداقل فقط برای همین کار بوت می‌شود (مثلاً لپ‌تاپ اختصاصی/USB زنده) و کد Ravix را دارد:
```bash
umask 077
python manage.py ravix_release keygen --key-file ~/ravix-keys/release-2026.key
```
- فایل با مجوز `600` ساخته می‌شود و **هرگز بازنویسی نمی‌شود** (کلید موجود ⇒ خطا).
- خروجی دو چیز چاپ می‌کند: `key_id` (۱۶ نویسهٔ hex؛ برای ابطال) و **کلید عمومی** (base64، آخرین خط). این دو را یادداشت کنید؛ محرمانه نیستند.
- **پشتیبان:** دست‌کم دو نسخهٔ رمزشده روی دو رسانهٔ جدا و دو مکان فیزیکی (مثلاً `age`/`gpg` + USB در گاوصندوق). گم‌شدن کلید = ناتوانی در انتشار تا زمان چرخش دستی کلید در همهٔ سایت‌ها؛ لو رفتنش = ابطال (بند ۶).
- کلید را در Git، CI، Secrets، ایمیل، چت، سرور سایت یا سرور به‌روزرسانی نگذارید. نام کلید را سال‌دار بگیرید تا چرخش روشن باشد.
- **آزمون سلامت:** `python manage.py ravix_release core --out /tmp/t --key-file ~/ravix-keys/release-2026.key` باید `امضاشده` چاپ کند.

### ۲. قراردادن کلید عمومی (و آدرس‌ها) در کد
تنها نقطهٔ ویرایش، فایل **`apps/core/official.py`** است:
```python
OFFICIAL_UPDATE_URL   = "https://updates.ravixcms.com"      # <URL>/updates/v1/index.json
OFFICIAL_REGISTRY_URL = "https://plugins.ravixcms.com"     # مخزن افزونه‌ها (docs/registry.md)
OFFICIAL_PUBLIC_KEYS  = (
    "release-2026:core:<کلید عمومی base64>",          # اصلی
    "release-spare-2026:core:<کلید عمومی base64>",   # یدک
)
REVOKED_KEY_IDS = ()
```
۱. آدرس‌ها از پیش پر شده‌اند (`ravixcms.com`)؛ فقط `OFFICIAL_PUBLIC_KEYS` را پر کنید (فقط کلید **عمومی**). **تا وقتی هیچ کلید مورد اعتمادی نباشد، منبع رسمی به‌روزرسانی و مخزن افزونه غیرفعال می‌مانند**: چیزی دانلود نمی‌شود و پنل به‌روزرسانی و «سلامت سایت» پیام روشن (نه خطا) نشان می‌دهند.
۲. آزمون `test_shipped_defaults_and_official_keys` در `apps/updater/tests/test_official_source.py` کلیدها و `key_id`هایشان را سنجاق می‌کند و عمداً با هر تغییر کلید می‌شکند تا آگاهانه مقدار تازه را ثبت کنید. `scripts/verify_release.py` (ZIP کامل) و `scripts/build_cpanel_zip.py` (بستهٔ cPanel) هم نبودِ دست‌کم دو کلید معتبر با دامنهٔ `core` در `apps/core/official.py` را خطا می‌گیرند.
   - **دامنهٔ اعتماد:** کلید رسمی فقط `core` است؛ یعنی فقط بستهٔ به‌روزرسانی هسته/قالب و `index.json` را تأیید می‌کند و **هرگز** بستهٔ افزونه را. کلید ناشر افزونه‌ها (`plugin_sign`) جداگانه با دامنهٔ `plugin` ثبت می‌شود (بخش «دامنهٔ اعتماد کلیدها» پایین).
۳. `python manage.py test`، commit و نسخه‌دهی طبق «انتشار» بالا.
۴. از این نسخه به بعد هر نصب تازه کلید شما را از کد می‌شناسد. **سایت‌های قدیمی‌تر** از نسخه‌ای که این کلید را ندارد نمی‌توانند خودکار ارتقا یابند (کلیدی ندارند که امضای شما را بسنجد): مدیرشان یک‌بار کلید را با `RAVIX_TRUSTED_PUBLIC_KEYS=release-2026:<کلید>` یا از پنل ← افزونه‌ها ← کلیدهای مورد اعتماد (مدیرکل) اضافه می‌کند، یا نسخهٔ جدید را دستی نصب می‌کند.
۵. کلید عمومی را **جدا و از کانال معتمد** هم منتشر کنید (صفحهٔ رسمی/Release notes)، تا کاربران بتوانند با کد مقایسه کنند.

### دامنهٔ اعتماد کلیدها (plugin / core)
هر کلید مورد اعتماد یک **دامنه** دارد و فقط در همان دامنه معتبر است:

| دامنه | چه چیزی را تأیید می‌کند | کلید معمول |
|---|---|---|
| `plugin` | بستهٔ افزونه (نصب دستی/وب و مخزن افزونه‌ها) | کلید ناشر افزونه‌ها (`plugin_sign`) |
| `core` | بستهٔ به‌روزرسانی هسته و قالب و `index.json` | کلیدهای رسمی `release-*` (`ravix_release`) |

- کلید `plugin` هسته را نمی‌تواند امضا کند و کلید `core` بستهٔ افزونه را؛ تلاش، خطای «این کلید برای این نوع بسته مورد اعتماد نیست» می‌دهد.
- شکل ورودی env: `publisher:plugin:<کلید>` ، `release:core:<کلید>` ، دو دامنه با `+` (`x:plugin+core:<کلید>`). شکل قدیمی `برچسب:کلید` همچنان کار می‌کند (هر دو دامنه) ولی `ravix_preflight` هشدار می‌دهد.
- کلیدهای ثبت‌شده در پنل (مدیرکل): فیلد «مورد اعتماد برای» هنگام ثبت (پیش‌فرض فقط افزونه) و قابل ویرایش در جدول. ردیف‌های پیش از این نسخه هر دو دامنه را نگه می‌دارند تا رفتارشان عوض نشود؛ مدیرکل آن‌ها را محدود کند. ابطال (`REVOKED_KEY_IDS`) بر همهٔ دامنه‌ها غالب است.
- روی سرور نمونه: `RAVIX_TRUSTED_PUBLIC_KEYS=publisher:plugin:<کلید ناشر افزونه‌ها>` ؛ کلیدهای رسمی هسته از کد می‌آیند و در env لازم نیستند.

### ۳. آدرس پیش‌فرض و تغییر با متغیر محیطی
| متغیر | پیش‌فرض | کار |
|---|---|---|
| `RAVIX_UPDATE_URL` | `official.OFFICIAL_UPDATE_URL` | آدرس سرور به‌روزرسانی؛ فقط `https://` (ریدایرکت دنبال نمی‌شود) |
| `RAVIX_UPDATE_DIR` | خالی | منبع پوشه‌ای/آفلاین؛ بر `RAVIX_UPDATE_URL` مقدم است |
| `RAVIX_OFFICIAL_REGISTRY_URL` | `official.OFFICIAL_REGISTRY_URL` | آدرس مخزن افزونه‌ها |
| `RAVIX_TRUSTED_PUBLIC_KEYS` | خالی | کلیدهای عمومی اضافه: `برچسب:دامنه:کلید` جداشده با ویرگول؛ دامنه `plugin`، `core` یا `plugin+core` (بدون دامنه = هر دو، قدیمی؛ `ravix_preflight` هشدار می‌دهد) |
| `RAVIX_REVOKED_KEY_IDS` | خالی | `key_id`های ابطال‌شده (ویرگول)؛ بر همهٔ منبع‌های کلید غالب است |
| `RAVIX_UPDATE_REQUIRE_SIGNED_INDEX` | `false` | `true`: فهرست بدون `index.json.sig` معتبر رد می‌شود (توصیه برای سرور رسمی) |
| `RAVIX_UPDATE_WEB_APPLY` | `false` | اعمال ارتقا از پنل (وگرنه فقط `ravix_update apply`) |

- **خاموش‌کردن** منبع رسمی روی یک سایت: متغیر را با مقدار **خالی صریح** بگذارید (`RAVIX_UPDATE_URL=`)؛ نبودنِ متغیر یعنی «پیش‌فرض کد».
- **آینه/سرور داخلی** (مثلاً شرکت با اینترنت محدود): `RAVIX_UPDATE_URL=https://mirror.corp.example` و همان فایل‌های امضاشده را آنجا بگذارید؛ چون اعتماد به امضاست نه میزبان، آینه نیازی به اعتماد ندارد.
- دانلود فقط از **همان دامنه** پذیرفته می‌شود. `download_url` در `index.json` که `ravix_release` می‌نویسد فقط نام فایل است و نسبت به `<URL>/updates/v1/` حل می‌شود.

### ۴. ساخت و میزبانی
روی همان ماشین امن، روی تگ منتشرشده (checkout تمیز):
```bash
python manage.py ravix_release core --out dist/ --key-file ~/ravix-keys/release-2026.key \
       --python '>=3.11' --min-from 0.6.0 --notes "خلاصهٔ تغییرات"
python manage.py ravix_release theme --theme-dir themes/mytheme --out dist/ --key-file ~/ravix-keys/release-2026.key
ls dist/    # index.json  index.json.sig  ravix-core-X.Y.Z.zip  ravix-theme-…zip
```
`index.json` با اجرای بعدی ادغام می‌شود (نسخه‌های قبلی حفظ می‌شوند)؛ پوشهٔ `dist/` را نگه دارید. سپس **فقط فایل‌های عمومی** را به سرور بفرستید:
```bash
rsync -av --delete-after dist/ deploy@updates.ravixcms.com:/var/www/updates/v1/
```
نمونهٔ Nginx (فایل‌های استاتیک؛ هیچ برنامه‌ای روی این میزبان اجرا نمی‌شود و هیچ کلیدی روی آن نیست):
```nginx
server {
    listen 443 ssl; http2 on; server_name updates.ravixcms.com;
    ssl_certificate     /etc/letsencrypt/live/updates.ravixcms.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/updates.ravixcms.com/privkey.pem;
    root /var/www;
    location /updates/v1/ {
        autoindex off;
        default_type application/octet-stream;
        types { application/json json; application/zip zip; }
        add_header Cache-Control "public, max-age=60";
        try_files $uri =404;     # ریدایرکت ندهید: کلاینت آن را رد می‌کند
    }
}
```
اگر `.well-known/acme-challenge` برای certbot لازم است، آن را در یک `server` پورت ۸۰ جدا بدهید. پیشنهاد: `RAVIX_UPDATE_REQUIRE_SIGNED_INDEX=true` را در `official`/محیط سایت‌ها فعال کنید و همیشه `index.json.sig` را همراه فهرست بفرستید.

### ۴ب. امضای آفلاین (کلید روی سرور یا روی فلش در ویندوز)
پروسهٔ وب، اجراگر به‌روزرسانی و افزونهٔ ناشر **هرگز** کلید خصوصی را نمی‌خوانند؛ فقط بستهٔ امضاشده را می‌پذیرند (آزمون خودکار این را نگه می‌دارد و `ravix_preflight` اگر `RAVIX_RELEASE_SIGNING_KEY` در محیط سایت باشد هشدار می‌دهد). ساخت و امضا دو گام جدا است:

**گام ۱ — ساخت بدون امضا** (روی هر ماشینی که کد را دارد؛ کلید لازم نیست):
```bash
python manage.py ravix_release core --out dist/ --no-sign --python '>=3.11' --min-from 0.9.0 \
       --notes "خلاصه" --notes-i18n notes.json --channel stable
```
`notes.json` یادداشت هر زبان است، مثل `{"fa": "…", "en": "…", "de": "…"}` (فقط زبان‌های Ravix؛ هر کدام حداکثر ۲۰۰۰ نویسه؛ مدیر به زبان خودش می‌بیند و اگر نبود انگلیسی). ساخت بسته وقتی `requirements.hashes.lock` با `requirements.lock` نخواند متوقف می‌شود: `python scripts/make_hash_lock.py` (به اینترنت نیاز دارد) را بزنید و فایل را در مخزن کامیت کنید.

**گام ۲ — امضای آفلاین** با `scripts/ravix_sign.py`؛ فقط Python ۳٫۹+ و بستهٔ `cryptography` می‌خواهد (نه Django، نه کد Ravix):

* روی سرور، با کلید در `/root/ravix-keys/` (مجوز ۶۰۰):
  ```bash
  pip install cryptography
  python3 scripts/ravix_sign.py dist/ --key-file /root/ravix-keys/release-2026.key
  ```
* روی ویندوز، با کلید روی فلش (مثلاً `E:\keys\release-2026.key`)؛ پوشهٔ `dist` را با بستهٔ بدون امضا و `scripts\ravix_sign.py` را روی ویندوز بگذارید:
  ```bat
  py -m pip install cryptography
  py scripts\ravix_sign.py dist --key-file E:\keys\release-2026.key
  ```
  سپس `dist` امضاشده را به سرور بفرستید (فلش را از ویندوز جدا کنید).

ابزار پیش از امضا SHA-256 هر بسته را با `index.json` مقایسه می‌کند (فایلِ عوض‌شده امضا نمی‌شود)، هر بستهٔ بدون امضا و خودِ `index.json` را امضا می‌کند و کلید خصوصی را چاپ یا کپی نمی‌کند. بررسی بدون کلید خصوصی: `python3 scripts/ravix_sign.py dist/ --check <کلید_عمومی>`. در ویندوز بررسی مجوز ۶۰۰ انجام نمی‌شود؛ فلش را رمزگذاری‌شده و فقط هنگام امضا وصل نگه دارید.

### ۴پ. میزبانی فایل‌ها: `https://ravixcms.com/updates/v1/` با nginx استاتیک
اول از همان دامنهٔ اصلی سرو می‌شود (زیردامنهٔ `updates.` بعداً اختیاری است و فقط با `RAVIX_UPDATE_URL` عوض می‌شود). روی سرور سایت ناشر:
```bash
sudo ./install.sh --serve-updates /srv/ravix/updates
```
`install.sh` پوشه را می‌سازد (مالک `ravix:www-data`، مجوز ۲۷۵۰)، `RAVIX_UPDATES_PUBLISH_DIR` را در `ravix.env` می‌گذارد و بلاک `location /updates/v1/` (فقط‌خواندنی، بدون فهرست پوشه) را **قبل از `location /`** اضافه می‌کند؛ روی فایلی که certbot تغییر داده هم با پشتیبان `.bak`، `nginx -t` و بازگشت خودکار. اجرای دوباره چیزی را تکرار نمی‌کند. پوشه نباید داخل `/srv/ravix/app` باشد. `ravix_preflight` وجود بلاک و قابل‌نوشتن بودن پوشه را می‌سنجد. سپس `dist/` امضاشده را در آن پوشه بگذارید (دستی با `rsync` یا افزونهٔ ناشر):
```bash
rsync -av --delete-after dist/ ravix-host:/srv/ravix/updates/
```
کانال غیر پایدار (مثلاً `beta`) در `/updates/v1/beta/` است و سایت با `RAVIX_UPDATE_CHANNEL=beta` آن را می‌خواند؛ `index.json` یک کانال دیگر رد می‌شود.

### ۵. چرخش کلید (برنامه‌ریزی‌شده، بدون ابطال اضطراری)
مثلاً سالانه یا هنگام تعویض نگه‌دارنده؛ کلید قدیمی هنوز **سالم** است:
1. کلید جدید را آفلاین بسازید (`release-2027.key`) و پشتیبان بگیرید.
2. در `official.py` کلید جدید را **اضافه** کنید و قدیمی را بگذارید بماند: `OFFICIAL_PUBLIC_KEYS = ("release-2026:…", "release-2027:…")`.
3. نسخهٔ N را با **کلید قدیمی** امضا و منتشر کنید. سایت‌ها با ارتقا به N هر دو کلید را می‌شناسند.
4. صبر کنید تا بیشتر سایت‌ها به N برسند (بازهٔ دلخواه؛ مثلاً ۳–۶ ماه).
5. از نسخهٔ N+1 با **کلید جدید** امضا کنید.
6. وقتی کلید قدیمی دیگر لازم نیست: در نسخهٔ بعدی (امضاشده با کلید جدید) شناسهٔ کلید قدیمی را به `REVOKED_KEY_IDS` اضافه کنید و از `OFFICIAL_PUBLIC_KEYS` بردارید. کلید خصوصی قدیمی را امن از بین ببرید.
ترتیب مهم است: اگر قبل از رسیدن سایت‌ها به N با کلید جدید امضا کنید، آن سایت‌ها آن را نمی‌پذیرند.

### ۶. ابطال کلید (اضطراری: کلید لو رفته یا مشکوک است)
ابطال یعنی «از این لحظه هر امضایی با این `key_id` رد شود»، حتی اگر کلید در کد، env یا پنل باشد. سه لایه، از سریع به کند:
| لایه | کجا | سرعت و محدودیت |
|---|---|---|
| پنل | پنل ← کلیدهای مورد اعتماد ← «غیرفعال/حذف» (فقط کلیدهای ثبت‌شده در پنل) | فوری، فقط همان سایت؛ کلید داخل کد را غیرفعال نمی‌کند |
| env | `RAVIX_REVOKED_KEY_IDS=<key_id>` و ری‌استارت | فوری، هر سایت جداگانه؛ **همهٔ منبع‌ها** (کد، env، پنل) را پوشش می‌دهد |
| کد | `official.REVOKED_KEY_IDS` در نسخهٔ جدید | سراسری، اما فقط با رسیدن آن نسخه به سایت |
ناگزیر محدودیت‌ها:
- **اگر کلید خصوصی لو رفته باشد، نسخهٔ ابطال‌کننده را نباید با همان کلید امضا کنید** (مهاجم هم می‌تواند). با کلید دوم/جدید که از قبل در `OFFICIAL_PUBLIC_KEYS` بوده امضا کنید. اگر فقط یک کلید داشتید و آن لو رفته، ارتقای خودکار دیگر امن نیست: سرور به‌روزرسانی را **همین الان خاموش/خالی** کنید، اطلاعیهٔ فوری بدهید و به مدیران بگویید `RAVIX_REVOKED_KEY_IDS` را بگذارند و کلید جدید را دستی اضافه کنند. (به همین دلیل از روز اول **دو کلید** بسازید: یک کلید فعال و یک کلید یدکِ آفلاین که از قبل در `official.py` ثبت شده است.)
- نسخه‌هایی که پیش‌تر با کلید لو رفته نصب شده‌اند ابطال نمی‌شوند؛ بررسی کنید مهاجم نسخه‌ای امضا و پخش کرده است یا نه (گزارش تاریخچهٔ سرور) و نسخهٔ پاک جدید بدهید.
- `key_id` را از خروجی `keygen`، پنل کلیدها یا `python -c "from apps.plugins.signing import key_id; print(key_id('<کلید عمومی>'))"` بگیرید.

### ۷. وقتی سرور به‌روزرسانی در دسترس نیست
**سایت هیچ خطایی نمی‌دهد.** رفتار تضمین‌شده (با آزمون‌های `apps/updater/tests/test_official_source.py`):
- **سایت عمومی** هرگز شبکهٔ بیرونی را صدا نمی‌زند (آزمون با mock که هر تماس شبکه را شکست می‌دهد).
- **پنل** فقط در صفحهٔ «به‌روزرسانی‌ها» و هنگام نصب/ارتقای افزونه از مخزن سراغ منبع می‌رود؛ قطع اتصال، خطای DNS، timeout، پاسخ ۴۰۴/۵۰۳، پاسخ مخدوش/غیر JSON/schema ناسازگار همه به یک پیام فارسی در همان صفحه تبدیل می‌شوند و کد پاسخ ۲۰۰ می‌ماند. داشبورد، «سلامت سایت» و بقیهٔ پنل اصلاً شبکه را لمس نمی‌کنند.
- **سقف انتظار** فهرست ۸ ثانیه است و **شکست ۵ دقیقه کش می‌شود** (برای افزونه‌ها ۶۰ ثانیه)، پس با قطع بودن سرور هر بازشدن صفحه معطل نمی‌ماند. دکمهٔ «بررسی» (`force`) کش شکست را دور می‌زند و پس از بازگشت سرور بلافاصله ریکاوری می‌شود.
- دستور `ravix_update check` با پیام خطای روشن و کد خروج غیر صفر تمام می‌شود (نه traceback)؛ ارتقای نیمه‌کاره‌ای رخ نمی‌دهد چون دانلود و بررسی SHA/امضا **پیش از** هر تغییر فایل انجام می‌شود.
- سایت‌هایی که آدرس را خالی گذاشته‌اند فقط پیام «منبع تنظیم نشده» می‌بینند.
برای پایش، `curl -fsS https://updates.ravixcms.com/updates/v1/index.json -o /dev/null` را در پایشگر خودتان بگذارید؛ از سمت سایت‌ها خطا گزارش نمی‌شود.

### ۸. چک‌لیست راه‌اندازی
- [ ] کلید فعال + کلید یدک ساخته، دو نسخهٔ رمزشده پشتیبان‌گیری شد.
- [ ] هر دو کلید عمومی در `OFFICIAL_PUBLIC_KEYS` و `key_id`ها یادداشت شد.
- [ ] `OFFICIAL_UPDATE_URL` و `OFFICIAL_REGISTRY_URL` پر شد؛ نسخه با آن‌ها منتشر شد.
- [ ] `ravix_release core` روی تگ تمیز اجرا و فایل‌ها به میزبان HTTPS رفت؛ `curl -I` هر چهار فایل ۲۰۰ می‌دهد.
- [ ] روی یک نصب آزمایشی: `ravix_update check` ← `ravix_update apply core --dry-run` ← `apply core`.
- [ ] آزمایش قطع سرور: آدرس را غلط بگذارید و ببینید پنل پیام می‌دهد و خطا نمی‌دهد.
- [ ] روش ابطال اضطراری را یک‌بار روی نصب آزمایشی تمرین کنید.

**آزموده نشده:** میزبانی واقعی با Nginx/Let's Encrypt؛ دانلود روی TLS واقعی (آزمون‌ها `http_get` را شبیه‌سازی می‌کنند ولی مسیر نسبی، امضا با کلید رسمی، ابطال و ارتقای کامل را واقعاً اجرا می‌کنند)؛ بازه‌های پیشنهادی چرخش.

## بستهٔ کاربر نهایی ≠ بستهٔ توسعه
- `git archive` فقط فایل‌های ردیابی‌شدهٔ git را می‌گیرد (پس `.env`، `db.sqlite3`، `media/`، `.venv`، کلیدها نمی‌آیند)؛ `.gitattributes` آزمون‌ها، `.github`، اسکریپت‌های آزمایش/انتشار و baseline bandit را هم حذف می‌کند و `verify_release.py` نبودنشان را می‌سنجد.
- `TZ=UTC` لازم است: زمان فایل‌های zip از منطقهٔ زمانی سازنده می‌آید؛ با آن، ساخت از یک commit همان SHA-256 را می‌دهد.

## راستی‌آزمایی توسط کاربر
```
sha256sum -c SHA256SUMS      # باید «ravix-X.Y.Z.zip: OK» بدهد
docker pull ghcr.io/ravixcms/ravixcms:X.Y.Z   # digest در متن Release آمده
```
`SHA256SUMS` فقط یکپارچگی دانلود را تضمین می‌کند؛ اعتماد به ناشر فقط با امضای دستی بالا و کلید عمومی معتبر حاصل می‌شود.

## تنظیمات یک‌بارهٔ مخزن GitHub
- Settings ← Actions ← General ← Workflow permissions: «Read repository contents» کافی است (permissionها در خود فایل‌ها تعیین شده‌اند).
- پس از اولین push image، Package را در GHCR با مخزن مرتبط کنید و در صورت نیاز public کنید.
- روی `main` قانون Branch protection بگذارید و jobهای `migrations`، `test`، `security` را Required کنید.
