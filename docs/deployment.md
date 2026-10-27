# راهنمای استقرار روی Ubuntu (۲۲٫۰۴ / ۲۴٫۰۴)

> نصب خودکار: `sudo ./install.sh` (همین مراحل را با پرسش انجام می‌دهد) یا Docker Compose — [`installer.md`](installer.md). این راهنما شرح دستیِ همان مراحل است.

معماری: **Nginx (HTTPS) → Gunicorn (سوکت یونیکس) → Django → PostgreSQL**. استاتیک با WhiteNoise/Nginx، مدیا با Nginx.

> این راهنما با اطلاعات واقعی شما (دامنه، سرور) اجرا می‌شود. هیچ دامنه، گواهی SSL یا سرویس خارجی از قبل متصل نشده است.
> آنچه در یک کانتینر لینوکس واقعاً اجرا و تأیید شده: Gunicorn (سوکت یونیکس) با تنظیمات production پشت Nginx با TLS (گواهی خودامضا)، PostgreSQL 16، تست‌ها روی PostgreSQL، پشتیبان‌گیری و **بازیابی واقعی** (`scripts/e2e_backup_restore.py`)، محدودیت آپلود در Nginx.
> آنچه تأیید **نشده**: certbot/Let's Encrypt روی دامنهٔ واقعی، systemd، و هر VPS یا میزبان تجاری مشخص ([`hosting.md`](hosting.md)). قبل از انتشار آن‌ها را روی سرور آزمایشی خودتان امتحان کنید.
> برای آزمودن کل این راهنما روی سرور آزمایشی خودتان: `sudo bash scripts/vps_smoke.sh` (راهنما در [`hosting.md`](hosting.md)).

## ۱. پیش‌نیازها
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-dev build-essential libpq-dev libjpeg-dev zlib1g-dev \
    postgresql nginx git certbot python3-certbot-nginx ufw
# ⚠ پیش از `ufw enable`: پورت واقعی SSH را بررسی کنید (`sudo sshd -T | grep ^port`). پروفایل OpenSSH فقط پورت ۲۲ را باز می‌کند؛
#   اگر SSH روی پورت دیگری است، همان را هم allow کنید وگرنه از سرور بیرون می‌افتید. فایروال ارائه‌دهنده هم باید ۸۰ و ۴۴۳ را باز بگذارد.
sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

## ۲. کاربر و پوشه‌ها
```bash
sudo adduser --system --group --home /srv/ravix ravix
sudo usermod -aG ravix www-data                    # nginx باید بتواند سوکت و media را بخواند
sudo mkdir -p /srv/ravix/{app,media,shop_private,plugin_data,plugins_installed,plugin_backups,themes_installed,update_work,update_backups} /etc/ravix /var/backups/ravix /var/cache/ravix
# (برای نصب افزونه از پنل: docs/plugin-installer.md)
# (نصب قالب ZIP از پنل — پیش‌فرض خاموش؛ فقط اگر می‌خواهید: sudo chown ravix:ravix /srv/ravix/themes_installed && sudo chmod 750 /srv/ravix/themes_installed ، سپس RAVIX_THEME_INSTALL_DIR و RAVIX_THEME_WEB_INSTALL را در /etc/ravix/ravix.env بگذارید؛ docs/themes.md)
sudo chown ravix:ravix /srv/ravix/plugin_data && sudo chmod 750 /srv/ravix/plugin_data   # فایل‌های خصوصی افزونه‌ها (nginx با X-Accel-Redirect می‌خواند)
sudo chown ravix:ravix /srv/ravix/plugins_installed /srv/ravix/plugin_backups && sudo chmod 750 /srv/ravix/plugins_installed && sudo chmod 700 /srv/ravix/plugin_backups
sudo chown ravix:www-data /srv/ravix/media /var/cache/ravix && sudo chmod 2750 /srv/ravix/media
# کد برنامه (به‌ویژه plugins/) فقط‌خواندنی برای سرویس است: sudo chown -R root:ravix /srv/ravix/app/plugins && sudo chmod -R g-w,o-rwx /srv/ravix/app/plugins
# سرویس‌های systemd با Group=www-data اجرا می‌شوند؛ پس فایل‌های unit باید SupplementaryGroups=ravix داشته باشند (هست) وگرنه پوشهٔ plugins خوانده نمی‌شود.
sudo chown ravix:ravix /var/backups/ravix && sudo chmod 700 /var/backups/ravix
```

## ۳. کد و وابستگی‌ها
```bash
sudo -u ravix git clone https://github.com/ravixcms/ravixcms.git /srv/ravix/app      # یا فایل zip را باز کنید
sudo -u ravix python3 -m venv /srv/ravix/venv
sudo -u ravix /srv/ravix/venv/bin/pip install -r /srv/ravix/app/requirements.lock   # نسخه‌های دقیقِ آزموده‌شده؛ یا requirements.txt برای آخرین نسخه‌های سازگار
```
پوشهٔ `plugins/` باید برای کاربر `ravix` **فقط‌خواندنی** باشد: `sudo chown -R root:ravix /srv/ravix/app/plugins && sudo chmod -R g-w,o-rwx /srv/ravix/app/plugins`.

## ۴. PostgreSQL
```bash
sudo -u postgres psql <<'SQL'
CREATE USER ravix WITH PASSWORD 'یک-رمز-قوی-و-تصادفی';
CREATE DATABASE ravix OWNER ravix ENCODING 'UTF8';
SQL
```
(دیتابیس را روی اینترنت باز نکنید؛ پیش‌فرض Ubuntu فقط localhost است.)

## ۵. متغیرهای محیطی
```bash
sudo install -m 640 -o root -g ravix /srv/ravix/app/.env.example /etc/ravix/ravix.env
sudo nano /etc/ravix/ravix.env
```
حداقل مقادیر:
```
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<خروجی: python -c "import secrets; print(secrets.token_urlsafe(60))">
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
DATABASE_URL=postgres://ravix:رمز@127.0.0.1:5432/ravix
RAVIX_SITE_URL=https://example.com
EMAIL_HOST=...  EMAIL_HOST_USER=...  EMAIL_HOST_PASSWORD=...     # SMTP واقعی شما
```
رمز عبور در `DATABASE_URL` را اگر نویسهٔ ویژه دارد URL-encode کنید. فایل `.env` یا این فایل را هرگز در مخزن کد نگذارید.

## ۶. مهاجرت، استاتیک، مدیرکل
```bash
cd /srv/ravix/app
sudo -u ravix bash -c 'set -a; . /etc/ravix/ravix.env; set +a;
  /srv/ravix/venv/bin/python manage.py migrate &&
  /srv/ravix/venv/bin/python manage.py collectstatic --noinput &&
  /srv/ravix/venv/bin/python manage.py createsuperuser &&
  /srv/ravix/venv/bin/python manage.py ravix_preflight'
```
`ravix_preflight` را تا بدون **خطا** شدن اصلاح کنید (هشدارها را بخوانید).

## ۷. سرویس Gunicorn
```bash
sudo cp /srv/ravix/app/deploy/ravix.service /etc/systemd/system/
sudo cp /srv/ravix/app/deploy/ravix-{expire-orders,publish-scheduled,backup}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ravix ravix-publish-scheduled.timer ravix-backup.timer
sudo systemctl enable --now ravix-expire-orders.timer      # فقط اگر فروشگاه روشن است (RAVIX_SHOP_ENABLED=true)
systemctl status ravix     # باید active (running) باشد
systemctl list-timers 'ravix-*'     # هر تایمر باید NEXT داشته باشد
```
لاگ: `journalctl -u ravix -f`.

**تعداد worker** خودکار است: `min(۲×CPU+۱، (رم − ۷۰۰MB) ÷ ۲۵۰MB)`، حداقل ۱ و حداکثر ۱۶؛ سقف کانتینر (cgroup) هم حساب می‌شود. سرور ۲ گیگ: ۳ worker با ۱ CPU و ۵ worker با ۲ CPU یا بیشتر. جایگزین دستی: `GUNICORN_WORKERS=N` (یا `WEB_CONCURRENCY`) در `/etc/ravix/ravix.env` و `GUNICORN_THREADS` (پیش‌فرض ۲)؛ تنظیم برآورد: `RAVIX_WORKER_MB` و `RAVIX_RESERVED_MB`. مقدار انتخاب‌شده هنگام شروع در لاگ می‌آید («Ravix: N workers × M threads»). `GUNICORN_KEEPALIVE` (پیش‌فرض ۶۵) باید از `keepalive_timeout` بالادست nginx (۵۵ ثانیه) بیشتر بماند.

### تایمرهای systemd
| تایمر | کار | زمان‌بندی | دستور |
|---|---|---|---|
| `ravix-publish-scheduled.timer` | نوشته‌های زمان‌بندی‌شدهٔ رسیده را «منتشرشده» می‌کند و رویداد انتشار را ثبت می‌کند | هر ۱ دقیقه (۱ دقیقه پس از بوت، سپس هر ۱ دقیقه پس از پایان اجرای قبلی) | `manage.py publish_scheduled` |
| `ravix-backup.timer` | پشتیبان کامل (دیتابیس + فایل‌ها) | روزانه ۰۳:۱۷ (تا ۵ دقیقه تأخیر تصادفی؛ اگر سرور آن ساعت خاموش بود پس از روشن‌شدن اجرا می‌شود) | `deploy/backup.sh`؛ **۷ پشتیبان آخر** نگه داشته می‌شود |
| `ravix-expire-orders.timer` | سفارش‌های پرداخت‌نشدهٔ منقضی را لغو و موجودی را آزاد می‌کند (فقط با فروشگاه) | هر ۵ دقیقه | `manage.py shop_expire_orders` |

- هر سرویس `Type=oneshot` با کاربر `ravix` و `EnvironmentFile=/etc/ravix/ravix.env` است؛ دو اجرای هم‌زمان از یک سرویس رخ نمی‌دهد.
- **نوشتهٔ زمان‌بندی‌شده بدون این تایمر هم هنگام نمایش «منتشرشده» حساب می‌شود**، ولی وضعیت ذخیره‌شده و رویداد انتشار فقط با `publish_scheduled` به‌روز می‌شود.
- نتیجهٔ آخرین اجرا: `journalctl -u ravix-publish-scheduled -n 20` و `journalctl -u ravix-backup -n 50`. اجرای دستی بی‌منتظرماندن زمان: `sudo systemctl start ravix-backup.service`.
- `ravix_preflight` وجود و فعال‌بودن (`enabled` و `active`) این تایمرها را بررسی می‌کند. تایمر غیرفعال **خطا** است. اگر systemd در دسترس نباشد (کانتینر/توسعه) فقط هشدار می‌دهد.
- اگر قبلاً پشتیبان را با cron گذاشته‌اید (`/etc/cron.d/ravix-backup`)، پس از فعال‌کردن تایمر آن را حذف کنید تا پشتیبان دو بار گرفته نشود: `sudo rm /etc/cron.d/ravix-backup`.

## ۸. Nginx
```bash
sudo cp /srv/ravix/app/deploy/ravix-proxy.conf /etc/nginx/ravix-proxy.conf
sudo cp /srv/ravix/app/deploy/nginx-ravix.conf /etc/nginx/sites-available/ravix
sudo sed -i 's/example.com/دامنه-شما.ir/g' /etc/nginx/sites-available/ravix
sudo ln -s /etc/nginx/sites-available/ravix /etc/nginx/sites-enabled/ravix && sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```
مسیر `alias` برای `/static/` باید به `STATIC_ROOT` (پیش‌فرض `/srv/ravix/app/staticfiles/`) و برای `/media/` به `/srv/ravix/media/` اشاره کند.
مطمئن شوید `MEDIA_ROOT` در تولید همان `/srv/ravix/media` است (در `config/settings/base.py` پیش‌فرض `BASE_DIR/media` است؛ یا پوشهٔ media را symlink کنید: `ln -s /srv/ravix/media /srv/ravix/app/media`).

## ۹. HTTPS (Let's Encrypt)
DNS دامنه باید از قبل به IP سرور اشاره کند.
```bash
sudo certbot --nginx -d دامنه-شما.ir -d www.دامنه-شما.ir
sudo certbot renew --dry-run          # تمدید خودکار
```
سپس `https://دامنه-شما.ir/healthz` باید `{"status":"ok"}` بدهد. HSTS و ریدایرکت HTTPS از سمت Django فعال است (preload و زیردامنه‌ها پیش‌فرض خاموش‌اند چون برگشت‌ناپذیرند؛ با `DJANGO_HSTS_PRELOAD` و `DJANGO_HSTS_INCLUDE_SUBDOMAINS` فقط آگاهانه روشن کنید)
(برای تست موقت بدون HTTPS: `DJANGO_SECURE_SSL_REDIRECT=false`).

## ۱۰. پشتیبان‌گیری و بازیابی
**پشتیبان خودکار روزانه** با `ravix-backup.timer` (بخش ۷) انجام می‌شود؛ cron لازم نیست. اجرای دستی: `sudo -u ravix /srv/ravix/app/deploy/backup.sh`.
هر اجرا در `/var/backups/ravix/` (دسترسی 700) این‌ها را می‌سازد (`STAMP` = تاریخ-ساعت):
`ravix-STAMP.dump` (PostgreSQL، فرمت custom؛ با `pg_restore --list` صحت‌سنجی می‌شود)، `…media.tar.gz`، `…shop_private.tar.gz` (فایل‌های دیجیتال فروشگاه)، `…plugin_data.tar.gz` (فایل‌های خصوصی افزونه‌ها)، `…plugins_installed.tar.gz`، `…plugin_backups.tar.gz`، `…registry.tar.gz` (هر کدام فقط اگر پوشه‌اش وجود داشته باشد) و `ravix-STAMP.sha256` (فهرست هش). فقط **۷ پشتیبان کامل آخر** می‌مانند (`KEEP_COUNT`، پیش‌فرض ۷؛ در `/etc/ravix/ravix.env` قابل تغییر؛ عدد صحیح ≥ ۱). فقط پشتیبانِ «کامل» (دارای `.sha256`) شمرده می‌شود، پس یک اجرای شکست‌خورده جای پشتیبان سالم را نمی‌گیرد؛ بقایای ناقصِ قدیمی‌تر از ۱ روز پاک می‌شوند. (جایگزین `KEEP_DAYS` قبلی است که بر پایهٔ سن فایل بود.)

**عمداً پشتیبان گرفته نمی‌شود:** `/etc/ravix/ravix.env`، `DJANGO_SECRET_KEY` و `RAVIX_SECRETS_KEY` (کلید رمزنگاری اسرار ذخیره‌شده). این‌ها را جداگانه و امن (مثلاً مدیر رمز) نگه دارید؛ **بدون `RAVIX_SECRETS_KEY` اسرار رمزشده در دیتابیس (مثل اطلاعات درگاه) پس از بازیابی خوانده نمی‌شوند.**
**پشتیبان را روی ماشین دیگر کپی کنید** (rsync/اسکریپت آف‌سایت)؛ پشتیبان روی همان سرور در برابر خرابی سرور بی‌فایده است.

**بازیابی:**
```bash
sudo systemctl stop ravix
cd /var/backups/ravix
sudo -u ravix /srv/ravix/app/deploy/restore.sh ravix-STAMP.dump ravix-STAMP.media.tar.gz ravix-STAMP.shop_private.tar.gz
# ابتدا sha256 بررسی می‌شود (ناهمخوانی = خروج با کد ۲ و بدون هیچ تغییری)، سپس تأیید تایپی می‌گیرد
# (غیرتعاملی: RAVIX_RESTORE_YES=1). دیتابیس با pg_restore --clean --single-transaction جایگزین می‌شود.
sudo -u ravix bash -c 'set -a; . /etc/ravix/ravix.env; set +a; /srv/ravix/venv/bin/python manage.py migrate'
sudo systemctl start ravix
```
**هر ماه یک بار بازیابی را روی یک سرور/دیتابیس آزمایشی تمرین کنید**؛ پشتیبانِ بازیابی‌نشده تضمین نیست.
آزمون واقعی این چرخه (ساخت دادهٔ نمونه ← پشتیبان ← حذف کامل دیتابیس و فایل‌ها ← رد پشتیبان دست‌کاری‌شده ← بازیابی ← مقایسهٔ `dumpdata` و هش فایل‌ها ← ورود با رمز قبلی):
```bash
E2E_PG_URL='postgresql://USER:PASS@127.0.0.1:5432' python scripts/e2e_backup_restore.py   # کاربر باید CREATEDB داشته باشد
```

## ۱۱. به‌روزرسانی نسخه
**روش توصیه‌شده (بسته‌های امضاشده، پشتیبان و بازگشت خودکار):** `python manage.py ravix_update apply core` — راهنمای کامل، مدل امنیتی و بازیابی: [`upgrade.md`](upgrade.md). این روش نیازمند منبع به‌روزرسانی و کلید امضای خودتان است (`RAVIX_UPDATE_URL`، `RAVIX_TRUSTED_PUBLIC_KEYS`).

**روش دستی (وقتی منبع امضاشده ندارید):**
```bash
sudo -u ravix /srv/ravix/app/deploy/backup.sh                       # ۱) همیشه پیش از به‌روزرسانی
cd /srv/ravix/app && sudo -u ravix git pull                         # ۲) کد جدید
sudo -u ravix /srv/ravix/venv/bin/pip install -r requirements.txt   # ۳)
sudo -u ravix bash -c 'set -a; . /etc/ravix/ravix.env; set +a; /srv/ravix/venv/bin/python manage.py migrate && /srv/ravix/venv/bin/python manage.py collectstatic --noinput && /srv/ravix/venv/bin/python manage.py ravix_preflight'
sudo systemctl reload ravix
```
بازگشت: کد قبلی را checkout کنید و در صورت مهاجرت ناسازگار، پشتیبانِ مرحلهٔ ۱ را بازیابی کنید.

## ۱۲. اتصال درگاه پرداخت واقعی (وقتی اطلاعات مرچنت دارید)
نگاه کنید به [`docs/shop.md`](shop.md). تا آن زمان `ravix_preflight` هشدار می‌دهد و ثبت سفارش در حالت production غیرفعال است.

## عیب‌یابی
| نشانه | علت/راه‌حل |
|---|---|
| `/panel/plugins/` هشدار «پوشهٔ افزونه‌ها قابل خواندن نیست» یا `PermissionError` در لاگ؛ در `ravix_preflight` خطای «پوشهٔ کد … فهرست‌کردن ممکن نیست» | فرایند سرویس عضو گروه `ravix` نیست (پوشه `root:ravix` 750 است). `SupplementaryGroups=ravix` را در unit بگذارید (در نسخهٔ جدید همراه است): `sudo cp deploy/ravix*.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart ravix`؛ بررسی: `systemctl show ravix -p SupplementaryGroups -p Group` |
| ۵۰۲ از Nginx | `systemctl status ravix`؛ سوکت `/run/ravix/gunicorn.sock` و عضویت `www-data` در گروه `ravix` |
| `DisallowedHost` | دامنه در `DJANGO_ALLOWED_HOSTS` نیست |
| ۴۰۳ CSRF روی فرم‌ها | `DJANGO_CSRF_TRUSTED_ORIGINS` با `https://` و هدر `X-Forwarded-Proto` در Nginx |
| استاتیک ۴۰۴ | `collectstatic` اجرا نشده یا `alias` Nginx اشتباه |
| ریدایرکت بی‌نهایت | `X-Forwarded-Proto` در Nginx تنظیم نشده |


## فروشگاه: فایل‌های دیجیتال و خاموش‌کردن
- `RAVIX_SHOP_PRIVATE_ROOT` (پیش‌فرض `shop_private/`) باید **خارج از media عمومی و خارج از ریشهٔ استاتیک nginx** باشد و در پشتیبان‌گیری بیاید (`ravix_preflight` در صورت قرارگیری داخل media خطا می‌دهد). مالک: کاربر سرویس، مجوز ۷۵۰.
- با `RAVIX_SHOP_ENABLED=false` دستور `shop_expire_orders` هم وجود ندارد؛ تایمر systemd مربوط را غیرفعال کنید.
