# Ravix (راویکس)

سیستم مدیریت محتوای فارسی و راست‌چین با Python / Django 5.2 / DRF — نسخهٔ هسته: `0.6.1`.

**امکانات:** احراز هویت و نقش‌ها، پنل مدیریت، نوشته/صفحه/رسانه، سایت عمومی با قالب‌ها، صفحه‌ساز، افزونه‌ها (نصب فقط توسط مدیرکل)،
چندسایتی (اختیاری)، فروشگاه با فایل دیجیتال (اختیاری)، API نسخه ۱، به‌روزرسانی امضاشده با پشتیبان و بازگشت خودکار.

> **صادقانه:** درگاه پرداخت واقعی، منبع رسمی به‌روزرسانی، دامنه و گواهی SSL از قبل متصل نیستند؛ فقط درگاه آزمایشی (sandbox) وجود دارد.
> وضعیت آزمون‌ها و محدودیت‌ها: [`docs/STATUS.md`](docs/STATUS.md)، [`docs/hosting.md`](docs/hosting.md).

## نصب سریع برای توسعه
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
mkdir -p media
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py runserver
```
پنل: `http://127.0.0.1:8000/panel/` — SQLite فقط برای توسعه.

## نصب در تولید
معماری: Nginx (HTTPS) → Gunicorn (سوکت یونیکس) → Django → PostgreSQL.
راهنمای گام‌به‌گام Ubuntu: [`docs/deployment.md`](docs/deployment.md). خلاصه:
1. `cp .env.example /etc/ravix/ravix.env` و پر کردن `DJANGO_SETTINGS_MODULE=config.settings.production`، `DJANGO_SECRET_KEY` (≥۳۲ نویسه)، `DJANGO_ALLOWED_HOSTS`، `DJANGO_CSRF_TRUSTED_ORIGINS`، `DATABASE_URL` (PostgreSQL).
2. `pip install -r requirements.lock`، `mkdir media`، `manage.py migrate`، `collectstatic`، `createsuperuser`.
3. `manage.py ravix_preflight` و `manage.py check --deploy` باید بدون خطا/هشدار باشند.
4. Gunicorn با `deploy/gunicorn.conf.py` (یا `deploy/ravix.service`)، Nginx با `deploy/nginx-ravix.conf`، سپس certbot.

## مستندات
| موضوع | فایل |
|---|---|
| استقرار، پشتیبان‌گیری و بازیابی | [`docs/deployment.md`](docs/deployment.md) |
| ارتقا (به‌روزرسانی امضاشده، بازگشت) | [`docs/upgrade.md`](docs/upgrade.md) |
| توسعهٔ قالب | [`docs/themes.md`](docs/themes.md) |
| توسعهٔ افزونه | [`docs/plugins.md`](docs/plugins.md)، [`docs/installer.md`](docs/installer.md) |
| امنیت (چک‌لیست) | [`docs/security-checklist.md`](docs/security-checklist.md)، [`SECURITY.md`](SECURITY.md) |
| میزبانی سازگار | [`docs/hosting.md`](docs/hosting.md) |
| فروشگاه و درگاه‌ها | [`docs/shop.md`](docs/shop.md)، [`docs/payment-gateways.md`](docs/payment-gateways.md) |
| API | [`docs/api.md`](docs/api.md) |
| انتشار نسخه | [`docs/release.md`](docs/release.md) |

## آزمون
```bash
.venv/bin/python manage.py test --parallel 4                      # فروشگاه روشن
RAVIX_SHOP_ENABLED=false .venv/bin/python manage.py test          # فروشگاه خاموش (ترتیبی)
E2E_PG_URL='postgresql://USER:PASS@127.0.0.1:5432' python scripts/e2e_backup_restore.py
```
سایر مسیرها: `scripts/e2e_*.py`. مشارکت: [`CONTRIBUTING.md`](CONTRIBUTING.md). تغییرات: [`CHANGELOG.md`](CHANGELOG.md).

## مجوز
MIT — [`LICENSE`](LICENSE). اعلان‌های شخص ثالث (فونت‌ها): [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
