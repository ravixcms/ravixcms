# مستندات API (نسخهٔ v1)

پایه: `/api/v1/` — قالب JSON. احراز هویت: نشست (Session) مرورگر با CSRF؛ درخواست‌های نوشتنی باید هدر `X-CSRFToken` داشته باشند.
دسترسی‌ها بر پایهٔ «قابلیت» (capability) نقش کاربر است.

| مسیر | روش | دسترسی | شرح |
|---|---|---|---|
| `/api/v1/me/` | GET, PATCH | کاربر واردشده | پروفایل خود: `id, username, first_name, last_name, email, phone, bio, role, role_label, date_joined` (فیلدهای `id, username, role, role_label, date_joined` فقط‌خواندنی) |
| `/api/v1/users/` | GET | قابلیت `users.view` | فهرست کاربران |
| `/api/v1/site/` | GET / PATCH | GET عمومی؛ نوشتن فقط مدیر | `site_name, tagline, description, logo, favicon, contact_phone, address, footer_text, registration_open` |
| `/api/v1/dashboard/stats/` | GET | قابلیت داشبورد | آمار پنل |
| `/api/v1/system/versions/` | GET | `updates.view` | نسخهٔ هسته، قراردادها، قالب‌ها، افزونه‌ها و شمای دیتابیس (بدون مسیر/کلید) |
| `/api/v1/shop/products/` | GET | عمومی | محصولات فعال **سایتِ دامنهٔ درخواست**؛ صفحه‌بندی `?page=&page_size=` (پیش‌فرض ۲۰، حداکثر ۵۰) |
| `/api/v1/shop/orders/` | GET | قابلیت `shop.orders` + دسترسی به سایت فعال پنل | سفارش‌های سایت فعال |
| `/healthz` | GET | عمومی | `{"status":"ok"}` برای مانیتورینگ (وضعیت DB را هم می‌سنجد) |

## خطاها
`401/403` عدم دسترسی، `404` منبع نامعتبر یا متعلق به سایت دیگر (عمداً ۴۰۴ تا وجود منبع فاش نشود)، `400` اعتبارسنجی.

## نمونه
```bash
curl https://example.com/api/v1/shop/products/?page=1
curl -b cookies.txt -H "X-CSRFToken: ..." -X PATCH -d '{"first_name":"علی"}' -H 'Content-Type: application/json' https://example.com/api/v1/me/
```
افزونه‌ها می‌توانند API خود را زیر `/ext/<slug>/` ارائه کنند (نگاه کنید به `docs/plugins.md`).
> API توکنی/کلید API برای کاربران خارجی هنوز وجود ندارد (در بخش «باقی‌مانده» ذکر شده).
