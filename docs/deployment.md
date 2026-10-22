# راهنمای استقرار روی Ubuntu (۲۲٫۰۴ / ۲۴٫۰۴)

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
sudo adduser --system --group --home /srv/ravin ravin
sudo usermod -aG ravin www-data                    # nginx باید بتواند سوکت و media را بخواند
sudo mkdir -p /srv/ravin/{app,media,shop_private,plugins_installed,plugin_backups,themes_installed,update_work,update_backups} /etc/ravin /var/backups/ravin /var/cache/ravin
# (برای نصب افزونه از پنل: docs/installer.md)
# (نصب قالب ZIP از پنل — پیش‌فرض خاموش؛ فقط اگر می‌خواهید: sudo chown ravin:ravin /srv/ravin/themes_installed && sudo chmod 750 /srv/ravin/themes_installed ، سپس RAVIN_THEME_INSTALL_DIR و RAVIN_THEME_WEB_INSTALL را در /etc/ravin/ravin.env بگذارید؛ docs/themes.md)
sudo chown ravin:ravin /srv/ravin/plugins_installed /srv/ravin/plugin_backups && sudo chmod 750 /srv/ravin/plugins_installed && sudo chmod 700 /srv/ravin/plugin_backups
sudo chown ravin:www-data /srv/ravin/media /var/cache/ravin && sudo chmod 2750 /srv/ravin/media
sudo chown ravin:ravin /var/backups/ravin && sudo chmod 700 /var/backups/ravin
```

## ۳. کد و وابستگی‌ها
```bash
sudo -u ravin git clone <آدرس-مخزن-کد-شما> /srv/ravin/app      # یا فایل zip را باز کنید
sudo -u ravin python3 -m venv /srv/ravin/venv
sudo -u ravin /srv/ravin/venv/bin/pip install -r /srv/ravin/app/requirements.lock   # نسخه‌های دقیقِ آزموده‌شده؛ یا requirements.txt برای آخرین نسخه‌های سازگار
```
پوشهٔ `plugins/` باید برای کاربر `ravin` **فقط‌خواندنی** باشد: `sudo chown -R root:ravin /srv/ravin/app/plugins && sudo chmod -R g-w,o-rwx /srv/ravin/app/plugins`.

## ۴. PostgreSQL
```bash
sudo -u postgres psql <<'SQL'
CREATE USER ravin WITH PASSWORD 'یک-رمز-قوی-و-تصادفی';
CREATE DATABASE ravin OWNER ravin ENCODING 'UTF8';
SQL
```
(دیتابیس را روی اینترنت باز نکنید؛ پیش‌فرض Ubuntu فقط localhost است.)

## ۵. متغیرهای محیطی
```bash
sudo install -m 640 -o root -g ravin /srv/ravin/app/.env.example /etc/ravin/ravin.env
sudo nano /etc/ravin/ravin.env
```
حداقل مقادیر:
```
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=<خروجی: python -c "import secrets; print(secrets.token_urlsafe(60))">
DJANGO_ALLOWED_HOSTS=example.com,www.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://example.com,https://www.example.com
DATABASE_URL=postgres://ravin:رمز@127.0.0.1:5432/ravin
RAVIN_SITE_URL=https://example.com
EMAIL_HOST=...  EMAIL_HOST_USER=...  EMAIL_HOST_PASSWORD=...     # SMTP واقعی شما
```
رمز عبور در `DATABASE_URL` را اگر نویسهٔ ویژه دارد URL-encode کنید. فایل `.env` یا این فایل را هرگز در مخزن کد نگذارید.

## ۶. مهاجرت، استاتیک، مدیرکل
```bash
cd /srv/ravin/app
sudo -u ravin bash -c 'set -a; . /etc/ravin/ravin.env; set +a;
  /srv/ravin/venv/bin/python manage.py migrate &&
  /srv/ravin/venv/bin/python manage.py collectstatic --noinput &&
  /srv/ravin/venv/bin/python manage.py createsuperuser &&
  /srv/ravin/venv/bin/python manage.py ravin_preflight'
```
`ravin_preflight` را تا بدون **خطا** شدن اصلاح کنید (هشدارها را بخوانید).

## ۷. سرویس Gunicorn
```bash
sudo cp /srv/ravin/app/deploy/ravin.service /etc/systemd/system/
sudo cp /srv/ravin/app/deploy/ravin-{expire-orders,publish-scheduled,backup}.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ravin ravin-publish-scheduled.timer ravin-backup.timer
sudo systemctl enable --now ravin-expire-orders.timer      # فقط اگر فروشگاه روشن است (RAVIN_SHOP_ENABLED=true)
systemctl status ravin     # باید active (running) باشد
systemctl list-timers 'ravin-*'     # هر تایمر باید NEXT داشته باشد
```
لاگ: `journalctl -u ravin -f`.

### تایمرهای systemd
| تایمر | کار | زمان‌بندی | دستور |
|---|---|---|---|
| `ravin-publish-scheduled.timer` | نوشته‌های زمان‌بندی‌شدهٔ رسیده را «منتشرشده» می‌کند و رویداد انتشار را ثبت می‌کند | هر ۱ دقیقه (۱ دقیقه پس از بوت، سپس هر ۱ دقیقه پس از پایان اجرای قبلی) | `manage.py publish_scheduled` |
| `ravin-backup.timer` | پشتیبان کامل (دیتابیس + فایل‌ها) | روزانه ۰۳:۱۷ (تا ۵ دقیقه تأخیر تصادفی؛ اگر سرور آن ساعت خاموش بود پس از روشن‌شدن اجرا می‌شود) | `deploy/backup.sh`؛ **۷ پشتیبان آخر** نگه داشته می‌شود |
| `ravin-expire-orders.timer` | سفارش‌های پرداخت‌نشدهٔ منقضی را لغو و موجودی را آزاد می‌کند (فقط با فروشگاه) | هر ۵ دقیقه | `manage.py shop_expire_orders` |

- هر سرویس `Type=oneshot` با کاربر `ravin` و `EnvironmentFile=/etc/ravin/ravin.env` است؛ دو اجرای هم‌زمان از یک سرویس رخ نمی‌دهد.
- **نوشتهٔ زمان‌بندی‌شده بدون این تایمر هم هنگام نمایش «منتشرشده» حساب می‌شود**، ولی وضعیت ذخیره‌شده و رویداد انتشار فقط با `publish_scheduled` به‌روز می‌شود.
- نتیجهٔ آخرین اجرا: `journalctl -u ravin-publish-scheduled -n 20` و `journalctl -u ravin-backup -n 50`. اجرای دستی بی‌منتظرماندن زمان: `sudo systemctl start ravin-backup.service`.
- `ravin_preflight` وجود و فعال‌بودن (`enabled` و `active`) این تایمرها را بررسی می‌کند. تایمر غیرفعال **خطا** است. اگر systemd در دسترس نباشد (کانتینر/توسعه) فقط هشدار می‌دهد.
- اگر قبلاً پشتیبان را با cron گذاشته‌اید (`/etc/cron.d/ravin-backup`)، پس از فعال‌کردن تایمر آن را حذف کنید تا پشتیبان دو بار گرفته نشود: `sudo rm /etc/cron.d/ravin-backup`.

## ۸. Nginx
```bash
sudo cp /srv/ravin/app/deploy/ravin-proxy.conf /etc/nginx/ravin-proxy.conf
sudo cp /srv/ravin/app/deploy/nginx-ravin.conf /etc/nginx/sites-available/ravin
sudo sed -i 's/example.com/دامنه-شما.ir/g' /etc/nginx/sites-available/ravin
sudo ln -s /etc/nginx/sites-available/ravin /etc/nginx/sites-enabled/ravin && sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```
مسیر `alias` برای `/static/` باید به `STATIC_ROOT` (پیش‌فرض `/srv/ravin/app/staticfiles/`) و برای `/media/` به `/srv/ravin/media/` اشاره کند.
مطمئن شوید `MEDIA_ROOT` در تولید همان `/srv/ravin/media` است (در `config/settings/base.py` پیش‌فرض `BASE_DIR/media` است؛ یا پوشهٔ media را symlink کنید: `ln -s /srv/ravin/media /srv/ravin/app/media`).

## ۹. HTTPS (Let's Encrypt)
DNS دامنه باید از قبل به IP سرور اشاره کند.
```bash
sudo certbot --nginx -d دامنه-شما.ir -d www.دامنه-شما.ir
sudo certbot renew --dry-run          # تمدید خودکار
```
سپس `https://دامنه-شما.ir/healthz` باید `{"status":"ok"}` بدهد. HSTS و ریدایرکت HTTPS از سمت Django فعال است
(برای تست موقت بدون HTTPS: `DJANGO_SECURE_SSL_REDIRECT=false`).

## ۱۰. پشتیبان‌گیری و بازیابی
**پشتیبان خودکار روزانه** با `ravin-backup.timer` (بخش ۷) انجام می‌شود؛ cron لازم نیست. اجرای دستی: `sudo -u ravin /srv/ravin/app/deploy/backup.sh`.
هر اجرا در `/var/backups/ravin/` (دسترسی 700) این‌ها را می‌سازد (`STAMP` = تاریخ-ساعت):
`ravin-STAMP.dump` (PostgreSQL، فرمت custom؛ با `pg_restore --list` صحت‌سنجی می‌شود)، `…media.tar.gz`، `…shop_private.tar.gz` (فایل‌های دیجیتال فروشگاه)، `…plugins_installed.tar.gz`، `…plugin_backups.tar.gz`، `…registry.tar.gz` (هر کدام فقط اگر پوشه‌اش وجود داشته باشد) و `ravin-STAMP.sha256` (فهرست هش). فقط **۷ پشتیبان کامل آخر** می‌مانند (`KEEP_COUNT`، پیش‌فرض ۷؛ در `/etc/ravin/ravin.env` قابل تغییر؛ عدد صحیح ≥ ۱). فقط پشتیبانِ «کامل» (دارای `.sha256`) شمرده می‌شود، پس یک اجرای شکست‌خورده جای پشتیبان سالم را نمی‌گیرد؛ بقایای ناقصِ قدیمی‌تر از ۱ روز پاک می‌شوند. (جایگزین `KEEP_DAYS` قبلی است که بر پایهٔ سن فایل بود.)

**عمداً پشتیبان گرفته نمی‌شود:** `/etc/ravin/ravin.env`، `DJANGO_SECRET_KEY` و `RAVIN_SECRETS_KEY` (کلید رمزنگاری اسرار ذخیره‌شده). این‌ها را جداگانه و امن (مثلاً مدیر رمز) نگه دارید؛ **بدون `RAVIN_SECRETS_KEY` اسرار رمزشده در دیتابیس (مثل اطلاعات درگاه) پس از بازیابی خوانده نمی‌شوند.**
**پشتیبان را روی ماشین دیگر کپی کنید** (rsync/اسکریپت آف‌سایت)؛ پشتیبان روی همان سرور در برابر خرابی سرور بی‌فایده است.

**بازیابی:**
```bash
sudo systemctl stop ravin
cd /var/backups/ravin
sudo -u ravin /srv/ravin/app/deploy/restore.sh ravin-STAMP.dump ravin-STAMP.media.tar.gz ravin-STAMP.shop_private.tar.gz
# ابتدا sha256 بررسی می‌شود (ناهمخوانی = خروج با کد ۲ و بدون هیچ تغییری)، سپس تأیید تایپی می‌گیرد
# (غیرتعاملی: RAVIN_RESTORE_YES=1). دیتابیس با pg_restore --clean --single-transaction جایگزین می‌شود.
sudo -u ravin bash -c 'set -a; . /etc/ravin/ravin.env; set +a; /srv/ravin/venv/bin/python manage.py migrate'
sudo systemctl start ravin
```
**هر ماه یک بار بازیابی را روی یک سرور/دیتابیس آزمایشی تمرین کنید**؛ پشتیبانِ بازیابی‌نشده تضمین نیست.
آزمون واقعی این چرخه (ساخت دادهٔ نمونه ← پشتیبان ← حذف کامل دیتابیس و فایل‌ها ← رد پشتیبان دست‌کاری‌شده ← بازیابی ← مقایسهٔ `dumpdata` و هش فایل‌ها ← ورود با رمز قبلی):
```bash
E2E_PG_URL='postgresql://USER:PASS@127.0.0.1:5432' python scripts/e2e_backup_restore.py   # کاربر باید CREATEDB داشته باشد
```

## ۱۱. به‌روزرسانی نسخه
**روش توصیه‌شده (بسته‌های امضاشده، پشتیبان و بازگشت خودکار):** `python manage.py ravin_update apply core` — راهنمای کامل، مدل امنیتی و بازیابی: [`upgrade.md`](upgrade.md). این روش نیازمند منبع به‌روزرسانی و کلید امضای خودتان است (`RAVIN_UPDATE_URL`، `RAVIN_TRUSTED_PUBLIC_KEYS`).

**روش دستی (وقتی منبع امضاشده ندارید):**
```bash
sudo -u ravin /srv/ravin/app/deploy/backup.sh                       # ۱) همیشه پیش از به‌روزرسانی
cd /srv/ravin/app && sudo -u ravin git pull                         # ۲) کد جدید
sudo -u ravin /srv/ravin/venv/bin/pip install -r requirements.txt   # ۳)
sudo -u ravin bash -c 'set -a; . /etc/ravin/ravin.env; set +a; /srv/ravin/venv/bin/python manage.py migrate && /srv/ravin/venv/bin/python manage.py collectstatic --noinput && /srv/ravin/venv/bin/python manage.py ravin_preflight'
sudo systemctl reload ravin
```
بازگشت: کد قبلی را checkout کنید و در صورت مهاجرت ناسازگار، پشتیبانِ مرحلهٔ ۱ را بازیابی کنید.

## ۱۲. اتصال درگاه پرداخت واقعی (وقتی اطلاعات مرچنت دارید)
نگاه کنید به [`docs/shop.md`](shop.md). تا آن زمان `ravin_preflight` هشدار می‌دهد و ثبت سفارش در حالت production غیرفعال است.

## عیب‌یابی
| نشانه | علت/راه‌حل |
|---|---|
| ۵۰۲ از Nginx | `systemctl status ravin`؛ سوکت `/run/ravin/gunicorn.sock` و عضویت `www-data` در گروه `ravin` |
| `DisallowedHost` | دامنه در `DJANGO_ALLOWED_HOSTS` نیست |
| ۴۰۳ CSRF روی فرم‌ها | `DJANGO_CSRF_TRUSTED_ORIGINS` با `https://` و هدر `X-Forwarded-Proto` در Nginx |
| استاتیک ۴۰۴ | `collectstatic` اجرا نشده یا `alias` Nginx اشتباه |
| ریدایرکت بی‌نهایت | `X-Forwarded-Proto` در Nginx تنظیم نشده |


## فروشگاه: فایل‌های دیجیتال و خاموش‌کردن
- `RAVIN_SHOP_PRIVATE_ROOT` (پیش‌فرض `shop_private/`) باید **خارج از media عمومی و خارج از ریشهٔ استاتیک nginx** باشد و در پشتیبان‌گیری بیاید (`ravin_preflight` در صورت قرارگیری داخل media خطا می‌دهد). مالک: کاربر سرویس، مجوز ۷۵۰.
- با `RAVIN_SHOP_ENABLED=false` دستور `shop_expire_orders` هم وجود ندارد؛ تایمر systemd مربوط را غیرفعال کنید.
