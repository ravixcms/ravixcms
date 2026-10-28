<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="static/brand/ravix-logo-dark.svg">
    <img src="static/brand/ravix-logo.svg" alt="Ravix" width="320">
  </picture>
</p>

# Ravix (راویکس)

**سیستم مدیریت محتوای فارسی و راست‌چین** برای وبلاگ، سایت شرکتی و فروشگاه کوچک — با Python و Django. نسخهٔ `0.9.0`.

[ravixcms.com](https://ravixcms.com) · [مستندات](https://ravixcms.com/docs) · [کد منبع در GitHub](https://github.com/ravixcms/ravixcms) · [به‌روزرسانی‌ها](https://updates.ravixcms.com) · [افزونه‌ها و قالب‌ها](https://plugins.ravixcms.com)

سایت را نصب می‌کنید، با پنل فارسی می‌نویسید و منتشر می‌کنید؛ تاریخ شمسی، راست‌چین و فونت فارسی از همان ابتدا هست.

## نصب با یک دستور
در پوشهٔ برنامه (به Docker نیاز دارد):
```bash
git clone https://github.com/ravixcms/ravixcms.git && cd ravixcms
docker compose up -d --build
```
سپس `http://localhost/install/` را باز کنید: نام سایت و مدیرکل را وارد کنید، تمام. (کد امنیتی نصب: `docker compose exec ravix python manage.py ravix_install_token`.)

سرور اوبونتو ۲۲/۲۴ با دامنه و HTTPS رایگان: `sudo ./install.sh` — پیش از هر تغییر می‌پرسد و اجرای دوباره‌اش بی‌خطر است. جزئیات و سرور واقعی با Docker: [`docs/installer.md`](docs/installer.md).

> این دستورها در سندباکس با PostgreSQL، Gunicorn و Nginx واقعی آزموده شده‌اند؛ **ساخت image داکر، certbot/Let's Encrypt و Ubuntu 22.04 هنوز روی سرور واقعی آزموده نشده‌اند.** ([`docs/STATUS.md`](docs/STATUS.md))

## فرق با وردپرس
| | Ravix | وردپرس |
|---|---|---|
| زبان و پایه | Python / Django؛ بدون PHP | PHP |
| فارسی | پنل و سایت فارسی، راست‌چین و تاریخ شمسی از پایه | ترجمهٔ فارسی و راست‌چین هست؛ تاریخ شمسی معمولاً با افزونه |
| **قالب** | فقط HTML/CSS/JS؛ **هیچ کدی روی سرور اجرا نمی‌کند** و به دیتابیس و کاربر دسترسی ندارد | قالب کد PHP است |
| **افزونه** | هرگز خودکار نصب یا روشن نمی‌شود؛ مدیر مشخصات و فایل‌ها را می‌بیند و تأیید می‌کند؛ تغییر فایل بعد از تأیید افزونه را متوقف می‌کند؛ بستهٔ مخزن باید امضا داشته باشد | نصب با یک کلیک از مخزن رسمی |
| **به‌روزرسانی** | بسته با امضای ناشر و SHA-256 بررسی می‌شود، پیش از ارتقا پشتیبان گرفته می‌شود و خرابی وسط کار خودکار برمی‌گردد (ارتقا به‌طور پیش‌فرض با دستور روی سرور، نه دکمه) | از پنل با یک کلیک |
| **همراه هسته** | سئو و Open Graph، ریدایرکت، دیدگاه با ضداسپم، صفحه‌ساز، «سلامت سایت»، پشتیبان‌گیری روزانه، API نسخهٔ ۱؛ فروشگاه و چندسایتی اختیاری | ویرایشگر بلوکی و دیدگاه در هسته؛ سئو، ریدایرکت، پشتیبان و فروشگاه معمولاً با افزونه |
| اکوسیستم | **کوچک و جوان**: یک قالب، سه افزونهٔ نمونه | بسیار بزرگ |

**صادقانه، Ravix هنوز:** با قالب و افزونهٔ وردپرس سازگار نیست و ابزار انتقال از وردپرس ندارد؛ درگاه پرداخت واقعی ندارد (فقط درگاه آزمایشی؛ درگاه واقعی افزونه است)؛ نشانی‌های رسمی به‌روزرسانی (`updates.ravixcms.com`) و مخزن افزونه (`plugins.ravixcms.com`) در کد هست، ولی تا وقتی کلید عمومی ناشر تنظیم نشده **غیرفعال‌اند** (پنل پیام روشن می‌دهد، خطا نیست). راه‌اندازی سرور آن‌ها: [`docs/release.md`](docs/release.md)، [`docs/registry.md`](docs/registry.md).
اگر اکوسیستم بزرگ و ابزارهای آمادهٔ فراوان برایتان مهم‌تر است، فعلاً وردپرس انتخاب بهتری است.

## مستندات
| | |
|---|---|
| **برای مدیر سایت** (زبان ساده: نصب، نوشتن، رسانه، قالب، افزونه، پشتیبان، به‌روزرسانی) | [`docs/admin/`](docs/admin/README.md) |
| **برای توسعه‌دهنده** (ساخت قالب، ساخت افزونه، API) | [`docs/developer/`](docs/developer/README.md) |
| **نصب روی هاست اشتراکی cPanel** (مثل وردپرس، بدون root؛ fa / en / ۸ زبان دیگر) | [`docs/cpanel.fa.md`](docs/cpanel.fa.md)، [`docs/cpanel.md`](docs/cpanel.md) |
| فهرست همهٔ مستندات (استقرار، امنیت، فروشگاه، انتشار…) | [`docs/README.md`](docs/README.md) |

## توسعه و مشارکت
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.lock
.venv/bin/python manage.py migrate && .venv/bin/python manage.py runserver   # SQLite؛ فقط توسعه
.venv/bin/python manage.py test
```
مشارکت: [`CONTRIBUTING.md`](CONTRIBUTING.md) · گزارش مشکل: [Issues](https://github.com/ravixcms/ravixcms/issues) · تغییرات: [`CHANGELOG.md`](CHANGELOG.md) · امنیت: [`SECURITY.md`](SECURITY.md) (گزارش آسیب‌پذیری: security@ravixcms.com) · پشتیبانی: support@ravixcms.com.

## مجوز
MIT — [`LICENSE`](LICENSE). اعلان‌های شخص ثالث (فونت‌ها): [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
