# راهنمای اجرای آزمون دودی روی Ubuntu 24.04 تازه

هدف: یک نصب آزمایشی **قابل تکرار و قابل بررسی**. این راهنما ادعای «آمادهٔ انتشار» نیست؛ خروجی آن فقط شواهدی است که با آن مستندات و اسکریپت‌ها اصلاح می‌شوند.

> **وضعیت راستی‌آزمایی:** `scripts/vps_smoke.sh` تا این لحظه **روی هیچ VPS واقعی اجرا نشده**. آنچه تأیید شده: نحو (`bash -n`)، shellcheck، بررسی نحو تک‌تک مراحل تولیدشده، رد اجرا روی سرور «غیرتازه»، و تشخیص واقعی پشتیبان دست‌کاری‌شده با `backup.sh`/`restore.sh` روی PostgreSQL 16. systemd، apt، certbot، ufw و Nginx روی سرور تازه فقط روی سرور شما آزموده می‌شوند.

## ۱. پیش‌نیازها
| مورد | شرط |
|---|---|
| سرور | **آزمایشی و تازه** Ubuntu 24.04 (۲۲٫۰۴ هم مستند است) با دسترسی root/sudo. هرگز روی سرور عملیاتی یا سروری که داده دارد اجرا نکنید. |
| منابع | حداقل ۱ vCPU، ۱ گیگابایت RAM (۲ گیگابایت راحت‌تر است)، حدود ۵ گیگابایت دیسک آزاد |
| شبکه | خروجی اینترنت (apt، PyPI) |
| پیش از شروع | **یک snapshot از سرور تازه بگیرید** (اگر ارائه‌دهنده دارد). تنها راه برگشتِ کامل همین است. |
| فقط حالت کامل | دامنه/زیردامنهٔ **آزمایشی** که رکورد A آن از قبل به IP این سرور اشاره می‌کند (`getent hosts دامنه` باید همان IP را بدهد)؛ پورت‌های ۸۰ و ۴۴۳ در فایروال ارائه‌دهنده باز؛ یک ایمیل واقعی برای certbot |

## ۲. آماده‌سازی سرور تازه — گام‌به‌گام

**گام ۱ — اتصال و بررسی سیستم** (فقط می‌خواند، چیزی تغییر نمی‌کند):
```bash
ssh root@IP-سرور            # یا با کاربر عادی و سپس: sudo -i
lsb_release -d              # باید Ubuntu 24.04.x باشد
whoami                      # باید root باشد
df -h /                     # حداقل ~۵GB آزاد
free -m                     # RAM
```

**گام ۲ — انتقال زیپ و هش** (از کامپیوتر خودتان؛ فایل‌های `ravix-0.6.0-vps-smoke.zip` و `SHA256SUMS`):
```bash
scp ravix-0.6.0-vps-smoke.zip SHA256SUMS root@IP-سرور:/root/
```

**گام ۳ — بررسی هش و باز کردن زیپ** (روی سرور؛ فقط `/root` را تغییر می‌دهد. `unzip` در Ubuntu تازه ممکن است نباشد؛ این تنها بستهٔ پیش‌نیاز دستی است):
```bash
cd /root
apt-get update -qq && apt-get install -y -qq unzip       # تغییر: نصب یک بسته
sha256sum -c SHA256SUMS --ignore-missing                   # باید «ravix-0.6.0-vps-smoke.zip: OK» بدهد؛ غیر از این، ادامه ندهید
unzip -q ravix-0.6.0-vps-smoke.zip
cd /root/ravix-0.6.0
ls scripts/vps_smoke.sh docs/vps-smoke-runbook.md          # باید هر دو باشند
```

**گام ۴ — (توصیه‌شده) اجرا در پس‌زمینه تا قطع شدن SSH مراحل را نیمه‌کاره رها نکند:**
```bash
# حالت محلی (بدون دامنه)
nohup bash scripts/vps_smoke.sh > /root/ravin-vps-smoke.stdout 2>&1 &
# یا حالت کامل (با دامنهٔ آزمایشی) — به‌جای خط بالا:
# DOMAIN=test.example.com CERTBOT_EMAIL=you@example.com CERTBOT_STAGING=1 nohup bash scripts/vps_smoke.sh > /root/ravin-vps-smoke.stdout 2>&1 &

tail -f /root/ravin-vps-smoke.stdout                       # پیشرفت زنده؛ با Ctrl+C فقط نمایش قطع می‌شود، نه اسکریپت
```
اجرا حدود ۵ تا ۱۵ دقیقه طول می‌کشد (بیشترِ آن نصب بسته‌ها و `pip install`). پایان: خط «همه مراحل موفق بود» یا «N مرحله ناموفق بود».

اگر سرور تازه نباشد، اسکریپت **بدون هیچ تغییری** همان لحظه با کد ۳ متوقف می‌شود و دلیل را چاپ می‌کند.

**گام ۵ — جمع‌آوری گزارش** (پس از پایان):
```bash
cd /root/ravix-0.6.0
bash scripts/vps_smoke_check_log.sh          # باید «OK: ...» بدهد؛ اگر LEAK دید، لاگ را نفرستید
mkdir -p /root/report && cd /root/report
cp /root/ravin-vps-smoke.log /root/ravin-vps-smoke.stdout .
lsb_release -d > env.txt; uname -r >> env.txt; free -m >> env.txt; df -h / >> env.txt
systemctl status ravin --no-pager > systemd-ravin.txt 2>&1
journalctl -u ravin --no-pager -n 150 > journal-ravin.txt 2>&1
nginx -T 2>/dev/null | head -n 150 > nginx-T.txt
tar -czf /root/ravin-smoke-report.tgz -C /root report && ls -l /root/ravin-smoke-report.tgz
```
بسته را با `scp root@IP-سرور:/root/ravin-smoke-report.tgz .` بردارید و بفرستید. همراهش بنویسید: نام ارائه‌دهنده/نوع سرور، حالت اجرا (محلی/کامل). **پیش از ارسال** نگاهی به فایل‌ها بیندازید: رمز و کلید نباید باشد (تست بالا همین را بررسی می‌کند)، ولی دامنه و IP شما هست.

## ۳. چه چیزی را تغییر می‌دهد (پیش از اجرا بخوانید)
اسکریپت فقط **در مسیرهای زیر** می‌نویسد. ستون سوم می‌گوید برگشت چگونه است.

| مرحله | تغییر روی سرور | برگشت |
|---|---|---|
| بررسی تازه‌بودن | فقط می‌خواند | — |
| نصب بسته‌ها | `apt` بسته‌های Python، PostgreSQL، Nginx، certbot، ufw، rsync، … را نصب می‌کند و **سرویس‌های PostgreSQL و Nginx را راه می‌اندازد** | فقط snapshot |
| کاربر و پوشه‌ها | کاربر/گروه سیستمی `ravin`؛ `www-data` به گروه `ravin` اضافه می‌شود؛ پوشه‌های `/srv/ravin/*`، `/etc/ravin`، `/var/backups/ravin`، `/var/cache/ravin`؛ فایل `/etc/ravin/.vps-smoke-owned` | snapshot |
| کپی کد | `rsync` (بدون `--delete`) به `/srv/ravin/app`؛ مالکیت و مجوز پوشهٔ `plugins/` | snapshot |
| venv | `/srv/ravin/venv` و نصب `requirements.lock` از اینترنت | snapshot |
| PostgreSQL | **نقش `ravin` با رمز تصادفی و دیتابیس `ravin`** ساخته می‌شود (نقش/دیتابیس موجود → از همان ابتدا توقف) | snapshot |
| `ravin.env` | `/etc/ravin/ravin.env` با mode 640 (رازها تصادفی‌اند) | snapshot |
| migrate / collectstatic / مدیرکل / preflight | جدول‌های دیتابیس `ravin`؛ `staticfiles/`؛ کاربر آزمایشی `smoke` (رمز تصادفی، چاپ نمی‌شود؛ در پایان حذف می‌شود) | snapshot |
| systemd | کپی `ravin.service`، `ravin-expire-orders.{service,timer}` به `/etc/systemd/system`؛ **enable و start** سرویس و تایمر | snapshot |
| Nginx | `/etc/nginx/ravin-proxy.conf` و سایت `ravin`؛ **حذف لینک سایت default**؛ reload | snapshot |
| ufw (فقط `SMOKE_UFW=1`) | قوانین ufw + **فعال‌کردن فایروال**. پورت واقعی sshd هم باز می‌شود، ولی اگر SSH را از مسیر غیرمعمول (مثلاً پورت‌فوروارد) می‌زنید خودتان مطمئن شوید | `ufw disable` از کنسول ارائه‌دهنده |
| certbot (فقط حالت کامل) | **درخواست واقعی به Let's Encrypt** (با `CERTBOT_STAGING=1` به محیط آزمایشی)، تغییر سایت Nginx برای HTTPS، تایمر تمدید | snapshot |
| دادهٔ نشانه | کاربر `marker` و فایل‌های `media/smoke.txt`، `media/marker.txt` | در پایان با نام مشخص حذف می‌شوند |
| backup.sh | فایل‌های `/var/backups/ravin/ravin-*.{dump,media.tar.gz,sha256}` | می‌مانند (فقط دادهٔ آزمایشی) |
| آزمون دست‌کاری | پوشهٔ موقت `/var/backups/ravin/.smoke-tamper` (پس از آزمون پاک می‌شود) | — |
| **restore.sh** | **دیتابیس `ravin` را با پشتیبانِ چند ثانیه پیش جایگزین می‌کند** (`pg_restore --clean`) و `media` را از آرشیو برمی‌گرداند؛ **سرویس `ravin` موقتاً متوقف می‌شود** | فقط روی سرور تازه اجرا می‌شود؛ داده‌ای جز دادهٔ آزمایشی در آن نیست |
| restart | `systemctl restart ravin` | — |

اسکریپت **هیچ‌جا** `rm -rf` روی مسیرهای پروژه/داده، `DROP DATABASE`، `--delete` یا حذف فایل کاربران ندارد. حذف‌های آن: لینک سایت default، `smoke.txt`/`marker.txt`، پوشهٔ موقت tamper، و دو کاربر آزمایشی (با نام و پسوند ایمیل `@example.invalid`).

## ۴. متغیرهای اسکریپت
| متغیر | الزام | معنی |
|---|---|---|
| `DOMAIN` | اختیاری | با وجود آن: حالت کامل (HTTPS واقعی، `SECURE_SSL_REDIRECT=true`، `ravin_preflight` باید بدون خطا باشد). بدون آن: حالت «محلی» (HTTP، بدون certbot) |
| `CERTBOT_EMAIL` | الزامی با `DOMAIN` | ایمیل ثبت‌نام Let's Encrypt |
| `CERTBOT_STAGING=1` | اختیاری | محیط آزمایشی Let's Encrypt (گواهی معتبر نیست؛ تکرار بدون محدودیت نرخ) |
| `SMOKE_UFW=1` | اختیاری | فعال‌کردن ufw؛ بدون آن ufw دست نمی‌خورد |

رمزها و کلیدها (`DJANGO_SECRET_KEY`، رمز PostgreSQL، `RAVIN_SECRETS_KEY`) را خود اسکریپت تصادفی می‌سازد و فقط در `/etc/ravin/ravin.env` می‌نویسد. **SMTP پیکربندی نمی‌شود** (ایمیل فعلاً کنسولی است؛ `ravin_preflight` هشدار می‌دهد، نه خطا).

## ۵. خروجی مورد انتظار
هر مرحله یک خط `PASS`/`FAIL`/`SKIP`/`INFO` می‌دهد و در پایان خلاصه.
- **حالت محلی:** `ravin_preflight` به‌صورت `INFO` می‌آید (خطای HTTPS/HSTS/کوکی Secure در HTTP مورد انتظار است)؛ certbot/HTTPS و ufw به‌صورت `SKIP`.
- **حالت کامل:** `ravin_preflight` باید کد ۰ بدهد (هشدار SMTP/منبع به‌روزرسانی/درگاه مجاز است).
- **رد پشتیبان دست‌کاری‌شده:** `PASS` یعنی `restore.sh` دقیقاً با کد ۲ و پیام «هش پشتیبان نمی‌خواند» ردش کرد و دادهٔ نشانه برنگشت.
- **کد خروج اسکریپت:** تعداد مراحل ناموفق (۰ = همه موفق)؛ ۲ = پیش‌شرط ناقص (مثلاً بدون `CERTBOT_EMAIL` یا DNS ناقص)؛ ۳ = سرور تازه نیست؛ ۱ = یک مرحلهٔ ضروری شکست خورد و ادامه داده نشد.
- در پایان نسخهٔ پروژه، PostgreSQL، Nginx و Python چاپ می‌شود.

## ۶. بررسی لاگ و تشخیص خطا
لاگ کامل: `/root/ravin-vps-smoke.log`. هر مرحله با سطر `=== نام مرحله ===` شروع می‌شود.
```bash
grep -n '^=== ' /root/ravin-vps-smoke.log                  # فهرست مراحل و شماره سطرشان
grep -E '^(PASS|FAIL|SKIP|INFO)' /root/ravin-vps-smoke.stdout   # خلاصه
sed -n '/^=== نام-مرحلهٔ-ناموفق ===/,/^=== /p' /root/ravin-vps-smoke.log   # خروجی همان مرحله
```
**ترتیب بررسی:** (۱) اولین `FAIL` را پیدا کنید؛ خطاهای بعدی اغلب پیامد آن‌اند. (۲) خروجی همان مرحله در لاگ. (۳) اگر سرویس بود: `journalctl -u ravin -n 100 --no-pager`. (۴) اگر Nginx بود: `nginx -t` و `/var/log/nginx/error.log`.

| نشانه | علت محتمل | بررسی / راه‌حل |
|---|---|---|
| «نصب بسته‌ها» FAIL با «Could not get lock» | به‌روزرسانی خودکار اولیهٔ Ubuntu هنوز در حال اجراست (اسکریپت تا ~۵ دقیقه صبر می‌کند) | چند دقیقه صبر کنید؛ `systemctl is-active unattended-upgrades`؛ دوباره اجرا (مجاز است) |
| «venv و requirements.lock» FAIL | نبود اینترنت/PyPI یا کمبود RAM هنگام ساخت | آخرین سطرهای مرحله؛ `free -m`؛ `dmesg \| tail` برای OOM |
| «migrate» FAIL با خطای اتصال | رمز/آدرس دیتابیس یا PostgreSQL بالا نیست | `systemctl status postgresql`؛ `sudo -u postgres psql -c '\du'` |
| «systemctl enable --now ravin» FAIL | خطای تنظیمات (`ravin.env`)، مجوز پوشه‌ها، یا ماژول | `journalctl -u ravin -n 100`؛ `systemctl status ravin` |
| «سوکت Gunicorn» FAIL | `/run/ravin/gunicorn.sock` ساخته نشده یا `DisallowedHost`/۴۰۰ | `ls -l /run/ravin/`؛ `DJANGO_ALLOWED_HOSTS` در env با `Host` تست یکی باشد |
| «تنظیم Nginx» FAIL | `nginx -t` خطا داده (مثلاً تعریف `limit_req_zone`) | خروجی `nginx -t` در لاگ؛ `nginx -T` |
| «healthz از طریق Nginx» FAIL | کد غیر از 200/301/308: ۵۰۲ = سوکت/مجوز گروه `www-data`؛ ۴۰۰ = Host | `tail /var/log/nginx/error.log`؛ `id www-data` (باید عضو `ravin` باشد) |
| «media از Nginx» FAIL | مجوز پوشهٔ `media` یا alias | `ls -ld /srv/ravin/media`؛ باید `ravin:www-data` و mode 2750 باشد |
| «certbot --nginx» FAIL | DNS اشتباه، پورت ۸۰ بسته (فایروال ارائه‌دهنده)، محدودیت نرخ | `/var/log/letsencrypt/letsencrypt.log`؛ `curl -I http://دامنه` از بیرون |
| «HTTPS healthz» FAIL با staging | گواهی staging معتبر نیست | عمداً با `-k` تست می‌شود؛ اگر باز FAIL است، `systemctl status nginx` |
| «backup.sh» FAIL | مجوز `/var/backups/ravin` یا `pg_dump` | خروجی مرحله؛ `ls -ld /var/backups/ravin` |
| «رد پشتیبان دست‌کاری‌شده» FAIL | کد خروج غیر از ۲ یا دادهٔ نشانه برگشته | **جدی است**: یعنی بررسی هش کار نکرده؛ لاگ مرحله را بفرستید و چیزی را بازیابی نکنید |
| «restore.sh» FAIL | `pg_restore` خطا داد (`--exit-on-error`) | خروجی مرحله؛ دیتابیس ممکن است نیمه‌کاره نباشد (بازیابی در یک تراکنش است) |
| «دادهٔ نشانه بازگشته» FAIL | بازیابی ناقص (کاربر یا فایل) | خروجی مرحله؛ `ls /srv/ravin/media` |
| `FAIL ravin_preflight` (حالت کامل) | خطای واقعی پیکربندی | بخش `=== ravin_preflight ===` در لاگ، سطرهای `✘` |

**اجرای دوباره:** روی سروری که همین اسکریپت ساخته مجاز است (فایل `/etc/ravin/.vps-smoke-owned`). برای یک نتیجهٔ تمیز، بهتر است snapshot اولیه را بازگردانید.

## ۷. پس از اجرا
- نصب برای بررسی شما می‌ماند. فقط دادهٔ آزمایشی (بالا) حذف می‌شود؛ پشتیبان‌های `/var/backups/ravin` می‌مانند.
- **این اسکریپت چیزی را «آمادهٔ انتشار» اعلام نمی‌کند.** میزبان در جدول `hosting.md` فقط پس از اجرای واقعی و ثبت تاریخ/حالت/نتیجه اضافه می‌شود. پیش از ثبت، من نتیجه را بررسی می‌کنم.
- برای پاک‌سازی کامل سرور آزمایشی: snapshot را بازگردانید یا سرور را نابود کنید (اسکریپت حذف‌کننده عمداً وجود ندارد).

## ۸. آنچه این اسکریپت عمداً آزمون نمی‌کند
SMTP و ایمیل واقعی، درگاه پرداخت، پشتیبان آف‌سایت، بار و کارایی، به‌روزرسانی نسخه (`ravin_update`)، چند دامنه، راه‌اندازی مجدد خود سرور (reboot)، و پنل روی موبایل.
