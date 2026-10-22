"""
قرارداد استاندارد درگاه پرداخت Ravix.

هسته‌ٔ فروشگاه **هیچ منطق اختصاصی بانک/ارائه‌دهنده‌ای ندارد**؛ فقط این رابط را صدا می‌زند. هر ارائه‌دهنده در یک افزونهٔ مستقل
زیرکلاس ``PaymentGateway`` می‌سازد و با hook ‎«shop.gateways»‎ ثبت می‌کند (docs/payment-gateways.md).

قواعد ثابت (هسته اجرا می‌کند، درگاه نمی‌تواند دور بزند):
- نتیجهٔ پرداخت فقط از ``verify_transaction``/``inquire_status`` (تماس سرور-به-سرور با API رسمی) می‌آید؛ پارامترهای بازگشتی مرورگر
  (``params``) فقط «اشاره» هستند (مثلاً Authority) و هرگز وضعیت را تعیین نمی‌کنند.
- مبلغ، واحد پول و شناسه‌ٔ سفارش از سمت سرور با رکورد پرداخت مقایسه می‌شود.
"""
from dataclasses import dataclass, field
from enum import Enum


class GatewayError(Exception):
    """خطای عمومی درگاه. پیام برای کاربر/مدیر امن است و نباید شامل کلید یا داده‌ٔ محرمانه باشد."""


class GatewayUnavailable(GatewayError):
    """درگاه فعال/پیکربندی‌شده نیست."""


class GatewayNetworkError(GatewayError):
    """خطای موقت شبکه/سرویس (timeout، ۵xx). وضعیت پرداخت **نامعلوم** است؛ دوباره استعلام شود، نه ناموفق."""


class GatewayRejected(GatewayError):
    """ارائه‌دهنده درخواست را صراحتاً رد کرد (مثلاً کلید نامعتبر یا مبلغ غیرمجاز)."""


@dataclass(frozen=True)
class ConfigField:
    """یک فیلد تنظیمات درگاه. ``secret=True``: رمزنگاری می‌شود، هرگز نمایش/برگردانده نمی‌شود، خالی‌گذاشتن = بدون تغییر."""

    key: str
    label: str
    type: str = "text"  # text | url | choice | bool
    secret: bool = False
    required: bool = True
    help: str = ""
    default: str = ""
    choices: tuple = ()
    max_len: int = 200


@dataclass(frozen=True)
class Redirect:
    """چگونگی هدایت مشتری. برای POST فیلدها در فرم خودکار فرستاده می‌شوند. میزبان باید در ``redirect_hosts`` درگاه باشد."""

    url: str
    method: str = "GET"
    fields: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TransactionContext:
    """آنچه درگاه از تراکنش می‌داند. ``reference`` شناسهٔ یکتای ما (کلید idempotency سمت ارائه‌دهنده) است."""

    reference: str
    order_number: str
    amount: int
    currency: str
    description: str
    callback_url: str
    gateway_ref: str = ""
    test_mode: bool = False


class VerifyStatus(str, Enum):
    PAID = "paid"
    FAILED = "failed"  # ناموفق/لغو‌شده توسط مشتری
    PENDING = "pending"  # هنوز نهایی نشده


@dataclass
class CreateResult:
    redirect: Redirect
    gateway_ref: str = ""  # شناسهٔ تراکنش نزد ارائه‌دهنده (مثلاً Authority)
    raw: dict = field(default_factory=dict)  # فقط برای گزارش؛ پیش از ذخیره پاک‌سازی می‌شود


@dataclass
class VerifyResult:
    status: VerifyStatus
    amount: int = 0
    currency: str = ""  # اگر ارائه‌دهنده برنگرداند خالی بماند (هسته واحد رکورد را می‌پذیرد فقط وقتی درگاه ``reports_currency=False`` باشد)
    gateway_ref: str = ""
    order_ref: str = ""  # اگر ارائه‌دهنده شناسهٔ سفارش ما را برگرداند اینجا بگذارید؛ باید با reference برابر باشد
    raw: dict = field(default_factory=dict)

    @classmethod
    def paid(cls, amount, **kw):
        return cls(VerifyStatus.PAID, amount=amount, **kw)

    @classmethod
    def failed(cls, **kw):
        return cls(VerifyStatus.FAILED, **kw)

    @classmethod
    def pending(cls, **kw):
        return cls(VerifyStatus.PENDING, **kw)


class PaymentGateway:
    """
    رابط استاندارد. زیرکلاس‌ها فقط سه متد را پیاده می‌کنند:

    - ``create_transaction(ctx)`` ← ایجاد تراکنش نزد ارائه‌دهنده و تعیین ``Redirect`` (هدایت مشتری)
    - ``verify_transaction(ctx, params)`` ← تأیید پس از بازگشت مشتری، با API رسمی
    - ``inquire_status(ctx)`` ← بررسی وضعیت بدون دخالت مشتری (برای callbackِ ازدست‌رفته و دکمهٔ «استعلام» پنل)
    """

    name = ""  # شناسه: حروف کوچک/عدد/_ ، ۳ تا ۳۰ نویسه
    label = ""
    description = ""
    is_test = False  # درگاه شبیه‌ساز؛ در تولید پذیرفته نمی‌شود مگر صراحتاً مجاز شود
    supported_currencies = ("IRT", "IRR")
    reports_currency = False  # آیا verify واحد پول را برمی‌گرداند؟ اگر True و برنگرداند ← پرداخت «نیازمند بررسی» می‌شود
    redirect_hosts: tuple = ()  # میزبان‌های مجاز https برای هدایت؛ خالی = فقط مسیر نسبی داخل سایت
    config_fields: tuple = ()

    def __init__(self, config=None, test_mode=False):
        self._config = dict(config or {})
        self.test_mode = bool(test_mode)

    def __repr__(self):  # هرگز config چاپ نمی‌شود
        return f"<{type(self).__name__} {self.name}>"

    @property
    def config(self) -> dict:
        return self._config

    def create_transaction(self, ctx: TransactionContext) -> CreateResult:
        raise NotImplementedError

    def verify_transaction(self, ctx: TransactionContext, params: dict) -> VerifyResult:
        raise NotImplementedError

    def inquire_status(self, ctx: TransactionContext) -> VerifyResult:
        raise NotImplementedError

    @classmethod
    def validate_config(cls, config: dict) -> list[str]:
        """بررسی اضافیِ ترکیب تنظیمات؛ لیست پیام خطا (فارسی) یا خالی."""
        return []


BaseGateway = PaymentGateway  # نام قدیمی
