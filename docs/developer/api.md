# API — شروع سریع

سه API وجود دارد: **API هسته** (`/api/v1/`)، **API مخزن افزونه‌ها** (`/registry/v1/`) و **مسیرهای افزونه‌ها** (`/ext/<slug>/`). مرجع جدول‌ها: [`../api.md`](../api.md)، [`../registry.md`](../registry.md).

## API هسته (`/api/v1/`)
- قالب JSON. **احراز هویت: نشست مرورگر + CSRF** (هدر `X-CSRFToken` برای نوشتن). توکن/کلید API برای کاربران خارجی **هنوز وجود ندارد**.
- دسترسی بر پایهٔ قابلیت (capability) نقش کاربر؛ `401/403` بی‌دسترسی، `404` منبع نامعتبر **یا متعلق به سایت دیگر** (عمداً)، `400` اعتبارسنجی.

| مسیر | دسترسی | کار |
|---|---|---|
| `GET /api/v1/site/` | عمومی | نام، شعار، لوگو، تماس (نوشتن: فقط مدیر) |
| `GET/PATCH /api/v1/me/` | کاربر واردشده | پروفایل خود |
| `GET /api/v1/shop/products/?page=&page_size=` | عمومی | محصولات فعال سایتِ دامنهٔ درخواست |
| `GET /api/v1/system/versions/` | `updates.view` | نسخهٔ هسته/قالب/افزونه (بدون مسیر دیسک یا کلید) |
| `GET /healthz` | عمومی | `{"status":"ok"}` (وضعیت DB را هم می‌سنجد؛ برای مانیتورینگ) |

```bash
curl https://example.com/api/v1/site/
curl https://example.com/api/v1/shop/products/?page=1
# نوشتن با نشست:  کوکی sessionid + csrftoken از ورود، هدر X-CSRFToken
curl -b cookies.txt -H "X-CSRFToken: $CSRF" -H 'Content-Type: application/json' -X PATCH \
     -d '{"first_name":"علی"}' https://example.com/api/v1/me/
```

## API مخزن افزونه‌ها (`/registry/v1/`)
خواندن عمومی؛ انتشار با توکن ناشر.
```bash
curl 'https://plugins.ravixcms.com/registry/v1/plugins/?q=form&compatible=1'
curl  https://plugins.ravixcms.com/registry/v1/plugins/contact_form/
curl -H "Authorization: Bearer $TOKEN" -F package=@my_plugin.zip -F publish=1 https://plugins.ravixcms.com/registry/v1/publish/
```
هر نسخه `sha256`، `fingerprint`، `signature`، `signature_key_id` و `compatibility` دارد. دانلود فقط HTTPS و با هدر `X-Checksum-SHA256`؛ کلاینت‌ها باید هش و امضا را با **کلید مورد اعتماد خودشان** بسنجند، نه مقادیر ادعاشدهٔ پاسخ.

## مسیرهای افزونه
افزونه‌ها API خود را در `/ext/<slug>/…` ارائه می‌دهند (در `plugin.json` ← `public_routes`)؛ راهنما: [`plugin.md`](plugin.md).

## منبع به‌روزرسانی (برای ناشران)
فایل‌های استاتیک `/updates/v1/index.json` و `index.json.sig` و بسته‌ها؛ ساخت با `ravix_release`؛ قالب و امضا: [`../release.md`](../release.md).

## نکات رفتاری
- هر منبع بیرونی (مخزن/به‌روزرسانی) ممکن است در دسترس نباشد؛ کلاینت شما باید خطا و timeout را تحمل کند (هستهٔ Ravix: سقف ۸ ثانیه و کش شکست ۵ دقیقه).
