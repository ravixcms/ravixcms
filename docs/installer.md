# روش‌های نصب Ravix

سه روش نصب هست؛ همهٔ آن‌ها به یک مقصد می‌رسند: سایتی که **مدیرکل** دارد و صفحهٔ نصب آن برای همیشه بسته است.

| | برای که؟ | چه می‌کند | چه می‌خواهد |
|---|---|---|---|
| [۱. Docker Compose](#۱-docker-compose) | آزمایش سریع، یا سرور با Docker | Ravix + PostgreSQL + Redis + Nginx + پشتیبان دوره‌ای | Docker ≥ 24 و Compose v2 |
| [۲. `install.sh`](#۲-installsh-اوبونتو) | سرور خام Ubuntu 22.04/24.04/26.04 | همه‌چیز را روی خود سیستم نصب می‌کند (systemd، Nginx، Let's Encrypt) | root، دامنه‌ای که به سرور اشاره دارد |
| [۳. نصب‌کنندهٔ وب](#۳-نصب‌کنندهٔ-وب-نصب-۵-دقیقه‌ای) | گام آخرِ هر دو روش بالا؛ یا توسعه | نام سایت، مدیرکل، زبان، منطقهٔ زمانی | فقط یک مرورگر |

> نصب افزونه‌ها و قالب‌ها (از پنل) موضوع دیگری است: [`plugin-installer.md`](plugin-installer.md). ارتقای نسخه: [`upgrade.md`](upgrade.md).

## ۳. نصب‌کنندهٔ وب («نصب ۵ دقیقه‌ای»)

برنامه تا وقتی مدیرکل ساخته نشده، **هر مسیر** (به‌جز `/install/`، فایل‌های استاتیک و `/healthz`) را به `/install/` می‌برد. فرم این‌ها را می‌گیرد:

- **توکن نصب** (فقط در production، پایین را ببینید)
- **نام سایت**
- **زبان** — بر پایهٔ `settings.LANGUAGES`؛ امروز فقط فارسی دارد و ترجمهٔ دیگری همراه برنامه نیست (فهرست آماده است تا با افزودن ترجمه، گزینه‌ها خودکار بیایند)
- **منطقهٔ زمانی** (پیش‌فرض `Asia/Tehran`؛ همهٔ مناطق IANA)
- **نام کاربری، ایمیل و رمز عبور مدیرکل** (با همان اعتبارسنجی رمز Django)

با «نصب»: اگر مهاجرت‌ها اجرا نشده باشند همان‌جا اجرا می‌شوند، مدیرکل و تنظیمات اولیه در **یک تراکنش** ساخته می‌شود، وارد پنل می‌شوید و `/install/` از آن لحظه برای همیشه ۴۰۴ می‌دهد.
زبان و منطقهٔ زمانی را بعداً در «تنظیمات سایت» هم می‌شود عوض کرد؛ مقدارشان در `SiteSettings` است و در هر درخواست فعال می‌شود (ذخیرهٔ تاریخ‌ها همچنان UTC است).

### قفل شدن و امنیت
- **قفل:** نصب‌شده بودن با `SiteSettings.installed_at` ثبت می‌شود. سایتی که از قبل **حتی یک کاربر** دارد (مثلاً ارتقا از نسخهٔ قبل) هرگز نصب‌پذیر نیست؛ در اولین درخواست خودکار «نصب‌شده» علامت می‌خورد. نصب دوم، حتی هم‌زمان، رد می‌شود (قفل ردیف در تراکنش).
- **توکن نصب:** روی سرور تازهٔ عمومی، «اولین بازدیدکننده» نباید مدیرکل شود. پس در production فرم یک توکن می‌خواهد:
  1. اگر `RAVIX_INSTALL_TOKEN` تنظیم باشد همان؛
  2. وگرنه فایل `RAVIX_INSTALL_TOKEN_FILE` (پیش‌فرض `install_token` کنار کد، مجوز 600)؛ اگر نبود، تصادفی ساخته می‌شود و **در لاگ** چاپ می‌گردد؛
  3. دیدنش هر وقت: `python manage.py ravix_install_token`.

  پس از نصب فایل توکن پاک می‌شود. در حالت `DEBUG` (توسعه) توکن لازم نیست.
- `install.sh` و `docker/init-env.sh` توکن را خودشان می‌سازند و چاپ می‌کنند.
- Nginx (هر دو روش بالا) نرخ درخواست `/install/` را محدود می‌کند.
- خاموش‌کردن کامل: `RAVIX_INSTALLER_ENABLED=false` (آن‌وقت فقط `createsuperuser` یا `ravix_install` می‌ماند).

### بدون مرورگر: `ravix_install`
برای اسکریپت و CI؛ همان منطق، رمز فقط از متغیر محیطی (نه آرگومان، که در `ps` دیده می‌شود):
```bash
RAVIX_ADMIN_PASSWORD='رمز-قوی' python manage.py ravix_install \
  --site-name "سایت من" --language fa --timezone Asia/Tehran \
  --username admin --email admin@example.com --password-env RAVIX_ADMIN_PASSWORD
```
بدون `--password-env` رمز را تعاملی می‌پرسد. نصب‌شده باشد خطا می‌دهد.

### توسعه (روی کامپیوتر خودتان)
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.lock
python manage.py runserver        # SQLite پیش‌فرض؛ http://127.0.0.1:8000/install/ (بدون توکن، چون DEBUG)
```

## ۱. Docker Compose

سرویس‌ها: `ravix` (Gunicorn)، `db` (PostgreSQL 17)، `redis` (کش و محدودسازی نرخ)، `nginx`، و `backup` (پشتیبان دوره‌ای). فقط پورت Nginx بیرون باز است؛ دیتابیس و برنامه روی شبکهٔ داخلی‌اند.

### آزمایش محلی — یک دستور
```bash
docker compose up -d --build
docker compose logs ravix | grep "توکن نصب"      # یا: docker compose exec ravix python manage.py ravix_install_token
# مرورگر: http://localhost/install/
```
بدون هیچ پیکربندی بالا می‌آید: کلید Django یک‌بار ساخته و در volume نگه داشته می‌شود، و رمز دیتابیس پیش‌فرضِ داخلیِ compose است (بیرون منتشر نمی‌شود). **برای سرور واقعی از پیش‌فرض‌ها استفاده نکنید** — پایین را ببینید.

### سرور واقعی با HTTPS
```bash
./docker/init-env.sh example.com --tls        # .env با رمزهای تصادفی (و چاپ توکن نصب)
sudo certbot certonly --standalone -d example.com     # پیش از بالا آوردن compose؛ پورت ۸۰ باید آزاد باشد
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
# https://example.com/install/
```
`docker-compose.tls.yml` پورت ۴۴۳ را باز می‌کند، `/etc/letsencrypt` میزبان را فقط‌خواندنی می‌گیرد، HTTP را به HTTPS می‌برد و ریدایرکت HTTPS را در Django روشن می‌کند. تمدید گواهی: `sudo certbot renew && docker compose exec nginx nginx -s reload`.
اگر TLS را جای دیگری تمام می‌کنید (Cloudflare، لودبالانسر)، فقط `./docker/init-env.sh example.com` بدون `--tls` و `RAVIX_PUBLIC_URL=https://example.com` در `.env`؛ Nginx هدر `X-Forwarded-Proto` بالادستی را می‌پذیرد. *(بدون HTTPS روی دامنهٔ واقعی ورود کار نمی‌کند: کوکی‌ها در production «Secure» هستند؛ فقط روی `localhost` استثنا است.)*

### متغیرهای `.env` (همه اختیاری)
| متغیر | پیش‌فرض | معنی |
|---|---|---|
| `RAVIX_DOMAIN` | `localhost` | نام میزبان (ALLOWED_HOSTS و server_name) |
| `RAVIX_PUBLIC_URL` | `http://localhost` | آدرس عمومی (CSRF، sitemap) |
| `POSTGRES_PASSWORD` | `ravix` | فقط هنگام ساخت اولیهٔ volume دیتابیس اعمال می‌شود |
| `DJANGO_SECRET_KEY` | ساخت خودکار | خالی ⇒ در `/data/secret_key` نگه داشته می‌شود |
| `RAVIX_INSTALL_TOKEN` | تصادفی | توکن صفحهٔ نصب |
| `RAVIX_HTTP_PORT` / `RAVIX_HTTPS_PORT` | 80 / 443 | پورت‌های میزبان |
| `EMAIL_HOST` … | — | SMTP واقعی |
| `BACKUP_INTERVAL_HOURS` / `KEEP_COUNT` | 24 / 7 | زمان‌بندی و تعداد پشتیبان |
| `RAVIX_AUTO_MIGRATE` | 1 | `migrate` هنگام شروع سرویس |

### داده‌ها و volumeها
| volume | محتوا | در پشتیبان؟ |
|---|---|---|
| `pgdata` | دیتابیس | ✔ (`pg_dump`) |
| `ravix_media` | آپلودها (`/data/media`؛ Nginx فقط‌خواندنی می‌بیند) | ✔ |
| `ravix_data` | فایل‌های دیجیتال فروشگاه، افزونه/قالب نصب‌شده، کلید Django، توکن | ✔ (به‌جز `secret_key`) |
| `ravix_backups` | پشتیبان‌ها (`/backups`) | — |

### پشتیبان و بازیابی
سرویس `backup` هر `BACKUP_INTERVAL_HOURS` ساعت همان `deploy/backup.sh` را اجرا می‌کند (دامپ PostgreSQL + آرشیو فایل‌ها + `.sha256`، نگهداری ۷ پشتیبان آخر). فایل‌ها:
```bash
docker compose exec backup ls -l /backups
docker compose cp backup:/backups/. ./offsite-backups/      # حتماً به ماشین دیگر کپی کنید
docker compose exec backup /app/deploy/backup.sh            # پشتیبان فوری
```
بازیابی (جزئیات و هشدارها: [`deployment.md`](deployment.md) بخش ۱۰):
```bash
docker compose stop ravix nginx
docker compose exec -e RAVIX_RESTORE_YES=1 backup /app/deploy/restore.sh /backups/ravix-STAMP.dump /backups/ravix-STAMP.media.tar.gz
docker compose up -d
```
> `.env` و `DJANGO_SECRET_KEY` (و `RAVIX_SECRETS_KEY` اگر دارید) در پشتیبان **نیستند**؛ جدا و امن نگه دارید. کلید Django ورودِ نشست‌ها و اسرار رمزشدهٔ درگاه را می‌سازد؛ با از دست رفتنش اسرار خوانده نمی‌شوند.

### ارتقا
تصویر تغییرناپذیر است (کد فقط‌خواندنی)؛ ارتقای درجا از پنل کار نمی‌کند. نسخهٔ تازهٔ کد را بگیرید و `docker compose up -d --build`؛ `migrate` خودکار اجرا می‌شود. پیش از آن `docker compose exec backup /app/deploy/backup.sh`.

### نکته‌های عملیاتی
- `RAVIX_TRUSTED_PROXIES` در compose روی شبکه‌های خصوصی Docker تنظیم است چون برنامه فقط پشت Nginx همین compose در دسترس است؛ Nginx هدرهای `X-Forwarded-For`/`X-Real-IP` را تعیین می‌کند. اگر جلوی Nginx پراکسی/CDN دیگری هست، نشانی آن را هم به `RAVIX_TRUSTED_PROXIES` اضافه کنید تا IP واقعی (راست‌ترین نشانی غیرمورداعتماد) خوانده شود.
- نسخهٔ major تصویر PostgreSQL (`postgres:17`) باید با `postgresql-client` داخل Dockerfile (Debian trixie = 17) برابر یا بالاتر از آن بماند؛ وگرنه `pg_dump` از ناسازگاری نسخه خطا می‌دهد.
- بدون compose (`docker run`): خودتان `/data`، `/data/media`، `/backups` را به volume وصل کنید و `DATABASE_URL`، `REDIS_URL` را بدهید.

## ۲. `install.sh` (اوبونتو)

روی سرور خام Ubuntu 22.04، 24.04 یا 26.04، با root:
```bash
sudo ./install.sh                                                       # تعاملی؛ پیش از هر تغییر می‌پرسد
sudo ./install.sh --domain example.com --email me@example.com --yes    # غیرتعاملی
sudo ./install.sh --dry-run --domain example.com                       # فقط نشان بده چه می‌کرد
sudo RAVIX_CLI_LANG=fa ./install.sh                                    # پیام‌ها فارسی (یا: --lang fa)
```
پیام‌های `install.sh` به‌طور پیش‌فرض انگلیسی‌اند؛ `RAVIX_CLI_LANG=fa` (همان متغیر خط فرمان پایتون) یا `--lang fa` آن‌ها را فارسی می‌کند. ترجمه‌ها در `deploy/install.fa.tsv` هستند (هر خط: متن انگلیسی، TAB، ترجمه)؛ برای زبان دیگر فایل `deploy/install.<lang>.tsv` بگذارید. زبان بدون فایل انگلیسی می‌ماند.
پیش‌نیاز: DNS دامنه از قبل به IP سرور اشاره کند و پورت‌های ۸۰/۴۴۳ باز باشند.

### چه می‌کند (به ترتیب؛ هر مرحله اول وضعیت را می‌سنجد و اگر انجام شده باشد رد می‌شود)
1. فقط بسته‌های apt **ناقص** را نصب می‌کند (`python3-venv`، `postgresql`، `nginx`، `certbot`، …). اگر پایتون ≥ 3.11 نبود (Ubuntu 22.04) با پرسش جداگانه `python3.12` را از deadsnakes می‌گیرد.
2. کاربر سیستمی `ravix` (بدون ورود) و پوشه‌ها: `/srv/ravix/{app,media,shop_private,…}`، `/etc/ravix`، `/var/backups/ravix`، `/var/cache/ravix`.
3. کد را با `rsync` (بدون حذف و بدون لمس داده/media/تنظیمات) به `/srv/ravix/app` می‌برد و `plugins/` را برای کاربر سرویس فقط‌خواندنی می‌کند (`root:ravix` با مجوز 750). چون سرویس‌های systemd با `Group=www-data` اجرا می‌شوند، فایل‌های unit `SupplementaryGroups=ravix` دارند تا فرایند همین پوشه را بخواند؛ `ravix_preflight` و «سلامت سایت» این را با گروه‌های واقعی فرایند می‌سنجند.
4. virtualenv و `pip install -r requirements.lock` (فقط اگر `requirements.lock` عوض شده باشد).
5. نقش و دیتابیس PostgreSQL `ravix` با رمز تصادفی (اگر نقش از قبل هست و رمزش در env نیست، پیش از تغییر رمز می‌پرسد).
6. `/etc/ravix/ravix.env` (مجوز 640): کلید Django، `DATABASE_URL`، `ALLOWED_HOSTS`، `CSRF_TRUSTED_ORIGINS`، توکن نصب و مسیرها. **فقط کلیدهای غایب را می‌افزاید؛ مقدار موجود را هرگز عوض نمی‌کند.** استثنا فقط توکن نصب است: برای سایتی که از قبل نصب شده توکن ساخته نمی‌شود و توکن قدیمی (اگر مانده) از فایل پاک می‌شود.
7. `migrate` و `collectstatic` (با کاربر سرویس).
8. systemd: `ravix.service` و تایمرهای انتشار زمان‌بندی‌شده، پشتیبان روزانه و (اگر فروشگاه روشن است) لغو سفارش‌های پرداخت‌نشده.
9. Nginx: سایت و `ravix-proxy.conf` (فایل قبلی با `.bak.<زمان>` ذخیره می‌شود؛ اگر certbot فایل را تغییر داده بازنویسی نمی‌شود). هستهٔ بدون IPv6 را تشخیص می‌دهد. قالب: gzip برای متن (با `Vary`)، `/static/` با نام هش‌دار یک‌ساله و `immutable` (نام بدون هش فقط یک ساعت، تا نسخهٔ کهنه نماند)، `/media/` سی‌روزه با ETag، و keepalive به مرورگر و به Gunicorn.
10. Let's Encrypt: `certbot --nginx --redirect` (اگر گواهی هست رد می‌شود؛ DNS را هم می‌سنجد). ufw فقط اگر فعال بود و با پرسش، ۸۰/۴۴۳ را باز می‌کند (SSH را دست نمی‌زند). بعد از آن **HTTP/2** روشن می‌شود: nginx ≥ 1.25.1 با `http2 on;` و نسخه‌های قدیمی‌تر (Ubuntu 22.04 و 24.04) با پارامتر `http2` در `listen ... 443 ssl`؛ از فایل نسخهٔ پشتیبان می‌ماند و اگر `nginx -t` نپذیرد برگردانده می‌شود. روی سایتی که certbot قبلاً فایلش را تغییر داده فقط همین مورد اعمال می‌شود؛ برای قالب جدید gzip/کش: فایل `/etc/nginx/sites-available/ravix` را کنار بگذارید (`mv ... ravix.old`)، `sudo ./install.sh` را بزنید و سپس `sudo certbot install --cert-name دامنه --nginx` را اجرا کنید.

**فایل‌های خصوصی افزونه (`/_plugin_files/`).** `install.sh` در `ravix.env` مقدار `RAVIX_ACCEL_REDIRECT_PREFIX=/_plugin_files/` را می‌گذارد و برنامه فایل خصوصی افزونه (مثلاً دانلود نسخه) را با `X-Accel-Redirect` به Nginx می‌سپارد. اگر Nginx برای این پیشوند `location` نداشته باشد، هر دانلود ۴۰۴ می‌شود. قالب `deploy/nginx-ravix.conf` این بلاک را دارد (`internal` و `alias` از `RAVIX_PLUGIN_DATA_DIR`، با `/` پایانی)، و `install.sh` روی سایتی که certbot فایلش را تغییر داده هم همین بلاک را **قبل از `location /`** اضافه می‌کند: فایل پشتیبان `.bak.<زمان>`، سپس `nginx -t`، و در صورت خطا برگرداندن فایل قبلی. اجرای دوباره چیزی اضافه نمی‌کند: اگر `location` با همان پیشوند هست (مثلاً بلوک دستی)، فایل دست‌نخورده می‌ماند. پیشوند یا پوشه با نویسهٔ غیرعادی پذیرفته نمی‌شود. دستی: داخل بلاک `server` پورت ۴۴۳، قبل از `location /`:

```nginx
location /_plugin_files/ {
    internal;
    alias /srv/ravix/plugin_data/;
}
```

بررسی: `ravix_preflight` و صفحهٔ «سلامت سایت» وجود این `location` (با `internal` و `alias` درست) را در `/etc/nginx` می‌خوانند و اگر نباشد هشدار/وضعیت بحرانی می‌دهند؛ اگر پیکربندی خوانده نشود (کانتینر، nginx جای دیگر) «نامعلوم» می‌گویند و حدس نمی‌زنند (مسیر دلخواه: `RAVIX_NGINX_CONFIG=/مسیر/یک:/مسیر/دو`). Docker این مشکل را ندارد: compose هیچ `RAVIX_ACCEL_REDIRECT_PREFIX` نمی‌گذارد و فایل را خود Django می‌فرستد.
11. بررسی نهایی: `/healthz` و `ravix_preflight`؛ در پایان نشانی `/install/` و توکن را چاپ می‌کند. اگر سایت **از قبل نصب شده** باشد «گام آخر» و توکن چاپ نمی‌شود، فقط نشانی پنل.

لاگ کامل: `/var/log/ravix-install.log`. اجرای دوباره امن است (به‌روزرسانی کد، بازسازی unitها، تکمیل مرحلهٔ ناتمام مثل certbot).

### کاربر `ravix-deploy` و محیط نسخه‌دار (از ۰٫۱۰٫۰)
`install.sh` علاوه بر `ravix` یک کاربر سیستمی بدون ورود به نام `ravix-deploy` می‌سازد که **فقط** به‌روزرسانی را اعمال می‌کند: کد برنامه مال او (`ravix-deploy:ravix`، گروه فقط می‌خواند) و `/srv/ravix` را هم او می‌نویسد؛ سرویس وب نمی‌تواند کد یا symlink محیط را عوض کند. `venv` یک symlink به `venv-<نسخه>` است. نصب روی سرور قدیمی همین چیدمان را بدون نصب مجدد و بدون دست‌زدن به داده می‌سازد (محیط قدیمی با نام `venv.legacy-…` می‌ماند؛ پس از اطمینان پاکش کنید). واحدهای تازه: `ravix-update.path/.service/.timer` و `ravix-restart.path/.service`.

### گزینه‌ها
| گزینه | اثر |
|---|---|
| `--domain D` / `--email E` | دامنه؛ ایمیل Let's Encrypt |
| `--no-www` | `www.D` را نیاور |
| `--no-ssl` | بدون certbot — فقط وقتی TLS جای دیگری تمام می‌شود (Cloudflare، لودبالانسر). ورود فقط پشت HTTPS کار می‌کند |
| `--serve-updates DIR` | فقط سایت ناشر: Nginx فایل‌های امضاشدهٔ به‌روزرسانی داخل `DIR` را در `/updates/v1/` سرو می‌کند (بلاک امن و idempotent؛ `docs/release.md` §۴پ) |
| `--no-nginx` | Nginx را دست نزن (پراکسی را خودتان می‌سازید؛ سوکت: `/run/ravix/gunicorn.sock`) |
| `--no-systemd` | سرویس نساز (کانتینر) — اگر systemd در حال اجرا نباشد خودکار فعال می‌شود |
| `--source DIR` | مسیر کد (پیش‌فرض: کنار اسکریپت) |
| `-y` / `-n` | همه «بله» / فقط چاپ |

### پس از نصب
صفحهٔ `https://دامنه/install/` را با توکن چاپ‌شده باز کنید ([روش ۳](#۳-نصب‌کنندهٔ-وب-نصب-۵-دقیقه‌ای)). بعد از نصب موفق `sudo ./install.sh` را یک بار دیگر اجرا کنید (یا خط `RAVIX_INSTALL_TOKEN` را در `/etc/ravix/ravix.env` پاک کنید): اسکریپت می‌بیند سایت نصب شده و توکن را از فایل برمی‌دارد. (صفحهٔ وب نمی‌تواند این فایل را پاک کند؛ فقط root به آن دسترسی نوشتن دارد.) ارتقا، پشتیبان و بازیابی دستی: [`deployment.md`](deployment.md) و [`upgrade.md`](upgrade.md). `ravix_preflight` را بخوانید؛ روی سرور تازه چند هشدار طبیعی است (SMTP، درگاه پرداخت، منبع به‌روزرسانی).

## عیب‌یابی
| نشانه | علت / راه‌حل |
|---|---|
| همه‌چیز به `/install/` می‌رود | هنوز نصب نشده‌اید — فرم را کامل کنید. (اگر سایت قبلاً داشتید ولی دیتابیس را عوض کردید، `DATABASE_URL` را بررسی کنید.) |
| «توکن نصب نادرست است» | `python manage.py ravix_install_token` (Docker: `docker compose exec ravix python manage.py ravix_install_token`) |
| فرم نصب «اتصال به دیتابیس برقرار نیست» | `DATABASE_URL`؛ در Docker `docker compose logs db` |
| ورود پس از نصب ممکن نیست (HTTP) | production کوکی Secure می‌دهد؛ HTTPS لازم است |
| `403 CSRF` | `DJANGO_CSRF_TRUSTED_ORIGINS` / `RAVIX_PUBLIC_URL` با `https://` و دامنهٔ درست |
| `pg_dump: server version mismatch` | نسخهٔ major PostgreSQL بالاتر از کلاینت داخل تصویر است (بالا) |
| `nginx -t`: «Address family not supported» | هستهٔ بدون IPv6؛ `install.sh` خودکار حذف می‌کند، در Docker خطوط `listen [::]` را در قالب Nginx حذف کنید |

## آنچه تأیید شده و آنچه نه
**تأیید شده (اجرای واقعی):**
- نصب‌کنندهٔ وب: آزمون‌های واحد (قفل، توکن، تراکنش و بازگشت در خطا، CLI، منطقهٔ زمانی)؛ اجرای زنده روی SQLite؛ و روی **PostgreSQL 16 + Gunicorn + Nginx با تنظیمات production** (توکن اشتباه رد، نصب موفق، ورود خودکار، ۴۰۴ پس از نصب).
- `install.sh`: اجرای واقعی روی Ubuntu 24.04 (با PostgreSQL 16 و Nginx) تا migrate/collectstatic/پیکربندی Nginx و `ravix_preflight`؛ اجرای دوباره (idempotent)؛ `--dry-run`؛ پشتیبان‌گیری روی نصب حاصل؛ `shellcheck` بدون هشدار.
- Docker: `docker compose config` برای هر دو فایل؛ قالب‌های Nginx با nginx 1.24 (نحو، بازنویسی `X-Real-IP`، مسدودسازی اجرای فایل در `/media/`)؛ `entrypoint.sh`/`healthcheck.py` روی PostgreSQL واقعی (بیرون از کانتینر).

**تأیید نشده — پیش از تکیه روی آن‌ها روی سرور آزمایشی امتحان کنید:**
- **ساخت تصویر Docker و `docker compose up`** (در محیط توسعه daemon Docker نبود)، شامل `HEALTHCHECK`، volumeهای تو در تو و سرویس `backup` داخل کانتینر، و دستور `http2 on;` در قالب TLS (نیازمند nginx ≥ 1.25.1؛ تصویر `nginx:1.27` دارد).
- `install.sh` روی **Ubuntu 22.04** و مسیر deadsnakes، **systemd** (unitها و تایمرها فعال‌سازی نشدند)، و **certbot / Let's Encrypt** روی دامنهٔ واقعی.
- پایتون ≥ 3.11 به‌جز 3.13 (مجموعهٔ آزمون فقط روی 3.13 اجرا شده).

## بعد از نصب

- **محتوای نمونه:** نصب یک نوشته و دو برگه («درباره ما»، «تماس با ما») می‌سازد. هر کدام را ویرایش کنید، مال شما می‌شود؛ بقیه را از داشبورد با «حذف محتوای نمونه» پاک کنید (دستی هم قابل حذف‌اند).
- **راهنمای شروع** در داشبورد با لوگو، اولین نوشته و قالب خودکار تیک می‌خورد و با «بستن راهنما» برای همیشه بسته می‌شود.
- **صفحه‌های خطا:** ۴۰۴ با ظاهر قالب فعال؛ ۵۰۰ و حالت تعمیر مستقل از دیتابیس و فایل‌های static‌اند.
- **سلامت سایت** (پنل ← سیستم): برای دیدن وضعیت پشتیبان در نصب سیستمی، پوشهٔ پشتیبان باید برای کاربر سرویس خوانا باشد (یا `BACKUP_DIR` را در محیط سرویس بگذارید)؛ وگرنه آن مورد «نامشخص» نشان داده می‌شود. وضعیت timerها فقط روی سرور با systemd قابل سنجش است، نه در Docker.

## Ubuntu 26.04 LTS

`install.sh` پذیرفتهٔ 26.04 هم هست، با یک هشدار. مشخصات این نسخه: PostgreSQL 18 (پیش‌فرض)، پایتون 3.13، systemd 259 و `sudo` جدید (sudo-rs). نصب‌کننده به نسخهٔ PostgreSQL وابسته نیست، از `runuser` (نه `sudo`) استفاده می‌کند و پایتون ≥ 3.11 را خودش پیدا می‌کند، پس تغییری لازم نبود. فایل‌های unit در `deploy/` گزینهٔ حذف‌شده‌ای ندارند (تست خودکار این را می‌سنجد). کد در CI روی runner اوبونتو 26.04 (SQLite و preflight) آزموده می‌شود؛ **خود `install.sh` هنوز روی یک سرور واقعی 26.04 اجرا نشده** است. اگر `pg_dump` سرور 16 دارید، کلاینت هم‌نسخه لازم است (در CI: `scripts/ci_pg_client.sh`).
