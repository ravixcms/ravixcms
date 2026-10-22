# سیستم درگاه پرداخت Ravix

> **هیچ درگاه واقعی‌ای متصل نیست.** هسته فقط یک رابط استاندارد و یک شبیه‌ساز (sandbox) دارد. هر ارائه‌دهندهٔ واقعی (زرین‌پال، بانک و …) باید در **افزونهٔ مستقل** و فقط بر اساس مستندات رسمی و کلیدهای معتبر خودش پیاده شود. این پروژه هیچ پاسخ موفقی را جعل نمی‌کند: «پرداخت‌شده» فقط با استعلام معتبر از API ارائه‌دهنده ثبت می‌شود.

## معماری
```
مشتری ─ checkout ─► services.start_payment ─► PaymentGateway.create_transaction ─► Redirect ─► صفحهٔ بانک
مشتری ◄─ callback ─ services.finalize_payment ─► PaymentGateway.verify_transaction (سرور↔سرور) ─► اعتبارسنجی هسته ─► سفارش paid
زمان‌بند/پنل ─► services.inquire_payment ─► PaymentGateway.inquire_status
```
| لایه | مسئولیت |
|---|---|
| `apps/shop/payments/base.py` | رابط `PaymentGateway`، `TransactionContext`، `Redirect`، `VerifyResult`، خطاها |
| `apps/shop/payments/registry.py` | ثبت درگاه‌ها (کد + hook افزونه)، فعال‌بودن هر سایت، بارگذاری پیکربندی |
| `apps/shop/payments/vault.py` | رمزنگاری اسرار (Fernet)، پاک‌سازی گزارش‌ها (`redact`) |
| `apps/shop/payments/http.py` | کلاینت HTTP امن برای افزونه‌ها (https، بدون redirect، timeout، بدون IP خصوصی) |
| `apps/shop/services.py` | **همهٔ منطق اعتماد**: مبلغ، واحد، شناسه، idempotency، تغییر وضعیت سفارش |
| افزونهٔ درگاه | فقط ترجمهٔ سه عمل به API ارائه‌دهنده |

درگاه هیچ‌گاه سفارش را تغییر نمی‌دهد؛ فقط `VerifyResult` برمی‌گرداند و هسته تصمیم می‌گیرد.

## رابط `PaymentGateway`
```python
from apps.shop.payments import (ConfigField, CreateResult, GatewayNetworkError, GatewayRejected, PaymentGateway, Redirect, VerifyResult)

class MyGateway(PaymentGateway):
    name = "my_gateway"              # حروف کوچک/عدد/_ ، ۳ تا ۳۰ نویسه؛ یکتا
    label = "نام نمایشی"
    supported_currencies = ("IRT", "IRR")
    reports_currency = True          # اگر verify واحد پول را برمی‌گرداند (توصیه می‌شود)
    redirect_hosts = ("pay.provider.ir",)   # میزبان‌های https مجاز برای هدایت (می‌تواند در __init__ از config ساخته شود)
    config_fields = (
        ConfigField("merchant_id", "شناسه پذیرنده", secret=True),
        ConfigField("api_base", "آدرس API", type="url"),
    )

    def create_transaction(self, ctx):    # ← ایجاد تراکنش نزد ارائه‌دهنده + تعیین هدایت مشتری
        data = ...                          # self.config["merchant_id"] فقط اینجا در دسترس است
        return CreateResult(redirect=Redirect(url="https://pay.provider.ir/pay/TOKEN"), gateway_ref="AUTHORITY")

    def verify_transaction(self, ctx, params):   # ← پس از بازگشت مشتری؛ سرور↔سرور
        # params فقط «اشاره» است؛ شناسهٔ تراکنش را از ctx.gateway_ref بگیرید نه از params
        return VerifyResult.paid(amount, currency="IRT", gateway_ref="REF", order_ref=ctx.reference)

    def inquire_status(self, ctx):               # ← بررسی وضعیت بدون مشتری
        return VerifyResult.pending()
```
`ctx` (`TransactionContext`): `reference` (شناسهٔ یکتای ما؛ **به‌عنوان order_id و کلید Idempotency به ارائه‌دهنده بدهید**)، `order_number`، `amount` (عدد صحیح)، `currency`، `description`، `callback_url` (مطلق)، `gateway_ref`، `test_mode`.

### قواعد نگاشت وضعیت
- موفق → `VerifyResult.paid(amount, currency=..., gateway_ref=..., order_ref=...)`؛ ناموفق/لغو‌شده توسط مشتری → `failed()`؛ هنوز نهایی نشده → `pending()`.
- وضعیت ناشناخته را **هرگز** موفق نکنید: `GatewayError` بدهید (هسته آن را «نامعلوم» می‌داند).
- خطای شبکه/۵xx/timeout → `GatewayNetworkError` (وضعیت می‌ماند «آغازشده»؛ بعداً استعلام می‌شود). رد صریح (کلید غلط، مبلغ غیرمجاز) → `GatewayRejected`. `request_json` این نگاشت را خودش انجام می‌دهد.
- متن خطا باید امن باشد؛ کلید/توکن/پاسخ خام را داخل پیام نگذارید (هسته هم مقدارهای سرّی پیکربندی را از هر پیام پاک می‌کند).

## اعتبارسنجی‌هایی که هسته انجام می‌دهد (درگاه نمی‌تواند دور بزند)
هنگام موفقیت: `amount == payment.amount == order.total` (عدد صحیح)، واحد پول برابر رکورد (و اگر `reports_currency=True` و برنگشت ← مغایرت)، `order_ref` (اگر آمد) برابر `reference`، `gateway_ref` برگشتی برابر ذخیره‌شده. هر مغایرت ← پرداخت `needs_review` (پول ممکن است گرفته شده باشد؛ بررسی دستی) و **سفارش پرداخت‌شده نمی‌شود**. پیش از آغاز: سفارش در انتظار پرداخت، مبلغ مثبت، واحد پول پشتیبانی‌شده، آدرس هدایت مجاز.

## idempotency و جلوگیری از ثبت چندباره
1. هر تراکنش `reference` یکتا دارد (کلید Idempotency سمت ارائه‌دهنده).
2. کلیک/درخواست مکرر «پرداخت» تا ۱۵ دقیقه همان تراکنش را استفاده می‌کند (قفل ردیف سفارش) — تراکنش تازه نمی‌سازد؛ ایجادِ ناموفق به‌علت شبکه با همان `reference` تکرار می‌شود.
3. نهایی‌سازی با قفل `select_for_update` و فقط از `initiated`؛ callback تکراری فقط رخداد `duplicate` ثبت می‌کند و دوباره از ارائه‌دهنده نمی‌پرسد.
4. قیود دیتابیس: حداکثر یک پرداخت `succeeded` برای هر سفارش؛ هر `(gateway, gateway_ref)` فقط برای یک پرداخت. نقض ← `needs_review`.
5. پرداخت موفق برای سفارشِ لغو/مسترد/پرداخت‌شده ← `needs_review`.

## ذخیرهٔ امن تنظیمات
- فیلد `secret=True`: با Fernet رمز و در `GatewayConfig.secrets_encrypted` ذخیره می‌شود؛ فیلدهای غیرمحرمانه در `public_config`.
- کلید رمزنگاری **فقط در محیط**: `RAVIN_SECRETS_KEY` (تولید: `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`). چرخش: کلید جدید را اول و قبلی را بعد از آن (ویرگول) بگذارید. بدون کلید در حالت `DEBUG=False` ذخیرهٔ اسرار رد می‌شود؛ `ravin_preflight` هم خطا می‌دهد.
- جایگزین بدون دیتابیس: متغیر `RAVIN_GATEWAY_<نام>_<فیلد>` (مثل `RAVIN_GATEWAY_EXAMPLE_PAY_API_KEY`) بر مقدار پنل مقدم است.
- پنل هرگز مقدار محرمانه را نشان نمی‌دهد (ورودی password، خالی = حفظ، «حذف» جداگانه). مقدار محرمانه در پاسخ API، قالب‌ها، کد جاوااسکریپت، گزارش‌ها و لاگ ممیزی نیست (تست می‌کند).
- `repr` درگاه، config را چاپ نمی‌کند. اسرار را در `plugin.json`/تنظیمات افزونه نگذارید (آنجا رمز نمی‌شوند).

## گزارش (`PaymentEvent`)
هر تراکنش رخدادهای زمان‌دار دارد: ایجاد، هدایت، بازگشت مشتری، تأیید/استعلام، تغییر وضعیت، تکراری، خطا. داده پیش از ذخیره با `vault.redact` پاک می‌شود: کلیدهای حساس (key/secret/token/password/authorization/merchant/card/pan/cvv/…) ← `***`، مقدار پیکربندی سرّی از هر متن حذف، حداکثر ۳۰ مورد/۲۰۰ نویسه/عمق ۳. شماره کارت و CVV هرگز ذخیره نمی‌شود. در پنل صفحهٔ سفارش نمایش داده می‌شود؛ API عمومی/کارکنان `gateway_ref` را برنمی‌گرداند.

## فعال/غیرفعال و حالت آزمایشی (پنل ← فروشگاه ← درگاه‌های پرداخت)
- هر درگاه برای هر سایت جدا فعال/غیرفعال می‌شود؛ ظرفیت: فعال‌سازی فقط با تنظیمات کامل؛ درگاه آزمایشی (`is_test`) در تولید فعال نمی‌شود. مجوز: `shop.gateways` (مدیر و مدیرکل). هر تغییر در لاگ ممیزی (بدون مقدار محرمانه) ثبت می‌شود.
- **غیرفعال‌کردن درگاه تراکنش‌های باز را نمی‌بندد**: تأیید و استعلام برای پرداخت‌های آغازشده ادامه دارد.
- «حالت آزمایشی»: پرچم `test_mode` به درگاه داده می‌شود (مثلاً آدرس sandbox ارائه‌دهنده)؛ پرداخت‌ها `is_test` علامت می‌خورند و `preflight` هشدار می‌دهد.
- اگر چند درگاه فعال باشد، مشتری انتخاب می‌کند (فقط از فهرست فعال‌ها پذیرفته می‌شود). برای راحتی توسعه، وقتی برای `sandbox` هیچ رکوردی نیست و `DEBUG` روشن است (یا دمو مجاز شده) و `RAVIN_SHOP_PAYMENT_GATEWAY=sandbox`، شبیه‌ساز بدون تنظیم فعال است؛ در تولید هرگز.

## هدایت مشتری
`Redirect(url, method="GET"|"POST", fields={})`. آدرس باید مسیر داخلی یا `https://` روی یکی از `redirect_hosts` باشد، وگرنه تراکنش رد می‌شود. GET ← ۳۰۲. POST ← صفحهٔ فرم خودکار؛ سیاست CSP سایت (`form-action 'self'`) فقط برای همین پاسخ و فقط برای همان میزبان باز می‌شود.

## callback
`/shop/pay/callback/<reference>/` (GET یا POST، بدون CSRF چون بانک از دامنهٔ دیگر برمی‌گردد؛ امنیت از تأیید سرور↔سرور است). نرخ‌محدود. پارامترهای دریافتی به `verify_transaction(ctx, params)` می‌رسند ولی هیچ‌وقت وضعیت را تعیین نمی‌کنند.

## خطاها و بازیابی
| وضعیت | رفتار |
|---|---|
| شبکه هنگام ایجاد | تراکنش `initiated`؛ سفارش در checkout لغو و موجودی برمی‌گردد؛ تلاش مجدد از صفحهٔ سفارش همان `reference` را به کار می‌برد |
| شبکه هنگام تأیید | `initiated` می‌ماند، رخداد `error`؛ دکمهٔ «استعلام وضعیت» پنل و کار زمان‌بندی‌شده دوباره می‌پرسند |
| `shop_expire_orders` | پیش از لغو سفارش از درگاه استعلام می‌گیرد؛ پرداخت‌شده را لغو نمی‌کند؛ اگر درگاه در دسترس نباشد لغو را تا ۲۴ ساعت به تعویق می‌اندازد؛ تراکنش‌های بی‌نتیجهٔ قدیمی را می‌بندد |
| ارائه‌دهنده «ناموفق» | تراکنش `failed`؛ سفارش در انتظار می‌ماند و مشتری دوباره پرداخت می‌کند (تراکنش جدید) |

## ساخت افزونهٔ درگاه جدید (گام‌به‌گام)
1. پوشهٔ `plugins/<slug>/` با `plugin.json`:
```json
{"slug": "my_gateway", "name": "درگاه من", "version": "1.0.0", "description": "...", "author": "...", "api_version": 1, "ravin": ">=0.5.0,<1.0.0",
 "hooks": [{"name": "shop.gateways", "handler": "hooks:register", "priority": 50}]}
```
2. `hooks.py`:
```python
def register(gateways, api, **_):
    try:
        from .gateway import MyGateway
    except ImportError:          # ماژول فروشگاه خاموش است
        return gateways
    return {**gateways, MyGateway.name: MyGateway}
```
3. `gateway.py`: کلاس `PaymentGateway` طبق بالا؛ برای HTTP از `apps.shop.payments.http.request_json` استفاده کنید.
4. افزونه را نصب و **فعال** کنید (مدیر؛ امضا ≠ بی‌خطر بودن کد — پیش از تأیید کد را بخوانید: افزونهٔ درگاه به کلیدهای پرداخت دسترسی دارد). سپس در «درگاه‌های پرداخت» تنظیمات را کامل و فعال کنید.
5. تست: الگوی `apps/plugins/tests/test_example_pay.py` (سرویس‌دهندهٔ ساختگی با جایگزینی `http._send`): پرداخت موفق، ناموفق، pending، مغایرت مبلغ/واحد/شناسه، خطای شبکه، callback تکراری، کلید غلط.
6. پیش از خرید واقعی: مستندات رسمی، کلید معتبر، حالت آزمایشی خود ارائه‌دهنده، سپس مبلغ کم؛ و تطبیق دستی اولین تراکنش‌ها با پنل ارائه‌دهنده.

### افزونهٔ نمونه `plugins/example_pay`
یک **الگو** است که به API فرضی `ExamplePay` (آدرس‌های `.invalid`) وصل می‌شود و به هیچ سرویس واقعی متصل نیست. قرارداد فرضی در سرصفحهٔ `gateway.py` مستند است (ایجاد با Idempotency-Key، تأیید، استعلام، بازیابی با `order_id`). برای دیدن چرخهٔ دستی بدون شبکه از درگاه داخلی `sandbox` استفاده کنید.

## محدودیت‌های صادقانه
- درگاه واقعی نوشته/تست نشده است؛ نمونه فقط با سرویس ساختگی در تست اجرا می‌شود.
- استرداد (refund) از طریق درگاه در رابط نیست (ثبت دستی وضعیت، برگشت وجه جداگانه).
- webhook/IPN ارائه‌دهنده در رابط نیست؛ بازیابی با callback مشتری + استعلام دوره‌ای انجام می‌شود (در صورت نیاز می‌توان مسیر webhook را در افزونه با `finalize_payment(reference)` ساخت؛ همان اعتبارسنجی اعمال می‌شود).
- محافظت SSRF بر اساس resolve نام است و برابر DNS-rebinding کامل نیست؛ در تولید خروجی شبکه را در سطح فایروال محدود کنید.
- همزمانی واقعی روی PostgreSQL در این محیط اجرا نشده است (SQLite).
