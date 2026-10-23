"""منطق فروشگاه: سبد (نشست)، ثبت سفارش با رزرو موجودی و قیمت‌گذاری سمت سرور، پرداخت و تأیید سمت سرور، تغییر وضعیت با تاریخچه و انقضا."""
from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
import logging
from datetime import timedelta

from django.conf import settings
from apps.core.params import safe_ids
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from urllib.parse import urlsplit

from django.db import IntegrityError
from django.urls import reverse

from apps.public.seo import absolute_url

from . import gateways
from apps.core.recipient import current_language, language_for_order, use_language

from .models import Order, OrderEvent, OrderItem, Payment, PaymentEvent, Product
from .payments import TransactionContext, VerifyStatus, vault

logger = logging.getLogger("ravix.shop")

MAX_QTY = 20
MAX_LINES = 30


class ShopError(Exception):
    pass


class StockError(ShopError):
    pass


# ------------------------------------------------------------------ سبد
def _key(site):
    return f"shop_cart_{site.pk}"


def cart_raw(request, site) -> dict:
    data = request.session.get(_key(site)) or {}
    return data if isinstance(data, dict) else {}


def cart_set(request, site, product_id, qty):
    data = dict(cart_raw(request, site))
    if qty <= 0:
        data.pop(str(product_id), None)
    else:
        if str(product_id) not in data and len(data) >= MAX_LINES:
            raise ShopError(gettext("The cart is too large."))
        data[str(product_id)] = min(int(qty), MAX_QTY)
    request.session[_key(site)] = data


def cart_clear(request, site):
    request.session.pop(_key(site), None)


def cart_lines(request, site):
    """[{product, qty, unit_price, line_total, line_discount, ok}] فقط محصولات فعالِ **همین سایت**؛ قیمت (با تخفیف) همیشه از دیتابیس."""
    raw = cart_raw(request, site)
    ids = safe_ids((str(k) for k in raw), 500)
    products = {p.pk: p for p in Product.objects.filter(site=site, is_active=True, pk__in=ids)}
    lines = []
    for pid in ids:
        p = products.get(pid)
        if p is None:
            continue
        try:
            qty = max(1, min(int(raw[str(pid)]), MAX_QTY))
        except (TypeError, ValueError):
            continue
        if p.is_digital:
            qty = 1  # نسخهٔ دیجیتال برای هر خرید یکی است
        ok = (p.stock >= qty if p.track_stock else True) and (not p.is_digital or bool(p.digital_file))
        lines.append({"product": p, "qty": qty, "unit_price": p.final_price, "line_total": p.final_price * qty, "line_discount": p.discount_amount * qty,
                      "line_list": p.price * qty, "ok": ok})
    return lines, sum(l["line_total"] for l in lines)


def cart_summary(lines) -> dict:
    return {"subtotal": sum(l["line_list"] for l in lines), "discount": sum(l["line_discount"] for l in lines), "total": sum(l["line_total"] for l in lines),
            "needs_shipping": any(l["product"].needs_shipping for l in lines)}


def search_products(qs, *, q="", category="", kind="", sort=""):
    """جست‌وجو/فیلتر/مرتب‌سازی کاتالوگ. ورودی‌ها طول‌محدود و فقط مقادیر مجاز؛ ORM پارامترها را امن می‌کند."""
    from django.db.models import Q

    q = (q or "").strip()[:80]
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(category__name__icontains=q))
    if category:
        qs = qs.filter(category__slug=category[:90])
    if kind in Product.Kind.values:
        qs = qs.filter(kind=kind)
    order = {"price_asc": "price", "price_desc": "-price", "new": "-created_at", "name": "name"}.get(sort)
    return qs.order_by(order, "-id") if order else qs


# ------------------------------------------------------------------ تاریخچه
def record_event(order, kind, *, message="", from_status="", to_status="", actor=None):
    return OrderEvent.objects.create(order=order, kind=kind, message=message[:300], from_status=from_status, to_status=to_status,
                                     actor=actor if getattr(actor, "pk", None) else None)


# ------------------------------------------------------------------ سفارش
@transaction.atomic
def create_order(site, lines, data: dict, user=None) -> Order:
    """سفارش را می‌سازد. **قیمت/تخفیف/موجودی دوباره از دیتابیس خوانده می‌شود** (نه از داده‌ای که پیش‌تر در صفحه یا سبد بوده)."""
    if not lines:
        raise ShopError(gettext("The cart is empty."))
    authed = getattr(user, "is_authenticated", False)
    if not authed and not settings.SHOP_ALLOW_GUEST_CHECKOUT:
        raise ShopError(gettext("You must sign in to buy."))
    fresh = {p.pk: p for p in Product.objects.filter(site=site, is_active=True, pk__in=[l["product"].pk for l in lines])}
    subtotal = discount = total = 0
    reserved = []
    for l in lines:
        p = fresh.get(l["product"].pk)
        if p is None:
            raise StockError((gettext("“%(name)s” is no longer available.") % {"name": l['product'].name}))
        qty = 1 if p.is_digital else max(1, min(int(l["qty"]), MAX_QTY))
        if p.is_digital and not p.digital_file:
            raise ShopError((gettext("The file for product “%(name)s” is not ready.") % {"name": p.name}))
        if p.track_stock:
            # رزرو اتمیک: فقط اگر موجودی کافی باشد کم می‌شود (جلوگیری از فروش بیش از موجودی در درخواست‌های همزمان)
            if not Product.objects.filter(pk=p.pk, site=site, is_active=True, stock__gte=qty).update(stock=F("stock") - qty):
                raise StockError((gettext("The stock of “%(name)s” is not sufficient.") % {"name": p.name}))
        reserved.append((p, qty))
        subtotal += p.price * qty
        discount += p.discount_amount * qty
        total += p.final_price * qty
    needs_shipping = any(p.needs_shipping for p, _ in reserved)
    data = dict(data)
    if needs_shipping and not (data.get("address") or "").strip():
        raise ShopError(gettext("An address is required for shipping a physical product."))
    if not needs_shipping:
        data["address"] = ""
    order = Order.objects.create(site=site, currency=settings.SHOP_CURRENCY_CODE, subtotal=subtotal, discount_total=discount, total=total, needs_shipping=needs_shipping,
                                 user=user if authed else None, language=current_language(), **data)
    OrderItem.objects.bulk_create([
        OrderItem(order=order, product=p, name=p.name, kind=p.kind, list_price=p.price, unit_discount=p.discount_amount, unit_price=p.final_price,
                  quantity=q, line_total=p.final_price * q, tracked=p.track_stock) for p, q in reserved])
    record_event(order, OrderEvent.Kind.CREATED, message=(gettext("Order placed (%(value)s).") % {"value": (gettext("user") if authed else gettext("guest"))}), to_status=order.status, actor=user if authed else None)
    return order


def _restore_stock(order):
    for item in order.items.select_related("product"):
        if item.product_id and item.tracked:
            Product.objects.filter(pk=item.product_id, site=order.site).update(stock=F("stock") + item.quantity)


# ------------------------------------------------------------------ پرداخت
REUSE_MINUTES = 15  # پرداخت «آغازشده»ٔ تازه برای همان سفارش/درگاه دوباره استفاده می‌شود (کلیک مکرر ← تراکنش تکراری نمی‌سازد)
STALE_PAYMENT_HOURS = 24


def log_payment(payment, kind, message="", *, data=None, from_status="", to_status="", secrets=()):
    """رخداد تراکنش. داده پیش از ذخیره پاک‌سازی می‌شود: کلیدهای حساس، مقدارهای سرّی پیکربندی، طول و عمق."""
    return PaymentEvent.objects.create(payment=payment, kind=kind, message=vault.scrub(message, secrets)[:300], from_status=from_status, to_status=to_status,
                                       data=vault.redact(data or {}, secrets))


def _callback_url(request, payment):
    path = reverse("shop:callback", args=[payment.reference])
    return absolute_url(request, path) if request is not None else (settings.SITE_URL or "") + path


def _ctx(payment, order, request=None):
    return TransactionContext(reference=payment.reference, order_number=order.number, amount=payment.amount, currency=payment.currency,
                              description=(gettext("Order %(number)s") % {"number": order.number}), callback_url=_callback_url(request, payment), gateway_ref=payment.gateway_ref,
                              test_mode=payment.is_test)


def validate_redirect(gw, redirect):
    """هدایت فقط به مسیر داخلی یا https روی میزبان‌های اعلام‌شدهٔ خود درگاه مجاز است."""
    url = redirect.url or ""
    if redirect.method not in ("GET", "POST"):
        raise ShopError(gettext("The gateway redirect method is invalid."))
    if url.startswith("/") and not url.startswith("//"):
        return redirect
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname and parts.hostname.lower() in {h.lower() for h in gw.redirect_hosts}:
        return redirect
    raise ShopError(gettext("The gateway redirect address is not allowed."))


def start_payment(order, request, gateway_name=None):
    """تراکنش را (idempotent) می‌سازد و ``Redirect`` مشتری را برمی‌گرداند. فقط برای سفارش «در انتظار پرداخت»."""
    try:
        gw = gateways.get_gateway(gateway_name, site=order.site)
    except gateways.GatewayError as exc:
        raise ShopError(str(exc))
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)  # سریال‌سازی کلیک‌های همزمان
        if order.status != Order.Status.PENDING:
            raise ShopError(gettext("This order cannot be paid."))
        if order.total <= 0 or order.currency not in gw.supported_currencies:
            raise ShopError(gettext("This gateway does not support the currency of this order."))
        payment = order.payments.select_for_update().filter(status=Payment.Status.INITIATED, gateway=gw.name, amount=order.total, currency=order.currency,
                                                            created_at__gte=timezone.now() - timedelta(minutes=REUSE_MINUTES)).order_by("-id").first()
        if payment is None:
            payment = Payment.objects.create(order=order, gateway=gw.name, amount=order.total, currency=order.currency, is_test=gw.is_test or gw.test_mode)
            log_payment(payment, PaymentEvent.Kind.CREATED, (gettext("A transaction was created for gateway “%(name)s”.") % {"name": gw.name}), data={"amount": payment.amount, "currency": payment.currency},
                        to_status=payment.status)
        else:
            log_payment(payment, PaymentEvent.Kind.DUPLICATE, gettext("Duplicate payment request; the same transaction was reused."))
    sec = gateways._registry.secrets_of(gw)
    try:
        result = gw.create_transaction(_ctx(payment, order, request))
        redirect = validate_redirect(gw, result.redirect)
    except gateways.GatewayNetworkError:
        log_payment(payment, PaymentEvent.Kind.ERROR, gettext("Could not connect to the gateway; try again."), secrets=sec)
        raise ShopError(gettext("Could not connect to the gateway; please try again."))
    except (gateways.GatewayError, ShopError) as exc:
        _fail(payment, str(exc), sec)
        raise ShopError(gettext("The gateway did not accept the transaction; please try again or choose another gateway."))
    except Exception:  # noqa: BLE001 - باگ افزونهٔ درگاه نباید صفحه را خراب کند
        logger.exception(cli_gettext("Unexpected error in gateway %s"), gw.name)
        _fail(payment, gettext("Internal gateway error"), sec)
        raise ShopError(gettext("Internal gateway error; please try again later."))
    try:
        if result.gateway_ref and not payment.gateway_ref:
            payment.gateway_ref = str(result.gateway_ref)[:100]
            payment.save(update_fields=["gateway_ref"])
    except IntegrityError:
        _fail(payment, gettext("The gateway transaction ID is a duplicate."), sec)
        raise ShopError(gettext("The gateway returned a duplicate ID; please try again."))
    log_payment(payment, PaymentEvent.Kind.REDIRECT, (gettext("The customer was redirected to gateway “%(name)s”.") % {"name": gw.name}), data={"gateway_ref": payment.gateway_ref, "raw": result.raw}, secrets=sec)
    return redirect


def _fail(payment, reason, secrets=()):
    old = payment.status
    payment.status = Payment.Status.FAILED
    payment.save(update_fields=["status"])
    log_payment(payment, PaymentEvent.Kind.ERROR, reason, from_status=old, to_status=payment.status, secrets=secrets)


def _check(payment, order, result, gw) -> list[str]:
    """اعتبارسنجی سمت سرور نتیجهٔ درگاه؛ فهرست مغایرت‌ها (خالی = معتبر)."""
    problems = []
    try:
        amount = int(result.amount)
    except (TypeError, ValueError):
        amount = -1
    if amount != payment.amount or payment.amount != order.total:
        problems.append(gettext("Amount"))
    if result.currency:
        if result.currency.upper() != payment.currency:
            problems.append(gettext("Currency"))
    elif gw.reports_currency:
        problems.append(gettext("Currency (not returned)"))
    if result.order_ref and result.order_ref != payment.reference:
        problems.append(gettext("Order ID"))
    if result.gateway_ref and payment.gateway_ref and result.gateway_ref != payment.gateway_ref:
        problems.append(gettext("Gateway transaction ID"))
    return problems


def _run(payment, params, *, inquire):
    """Runs the state machine with every message it stores (order history is shown to the customer) in the order's language."""
    with use_language(language_for_order(payment.order)):
        return _run_in_language(payment, params, inquire=inquire)


def _run_in_language(payment, params, *, inquire):
    """ماشین حالت: ← (payment, order, outcome) ؛ outcome ∈ applied|pending|unknown|duplicate. تماس شبکه بیرون از قفل دیتابیس انجام می‌شود."""
    order = payment.order
    if payment.status != Payment.Status.INITIATED:
        log_payment(payment, PaymentEvent.Kind.DUPLICATE, gettext("Duplicate request for a finalized transaction; ignored."))
        return payment, order, "duplicate"
    try:
        gw = gateways.get_gateway(payment.gateway, site=order.site, require_enabled=False)  # درگاهِ غیرفعال‌شده هم باید تراکنش‌های باز را تأیید کند
    except gateways.GatewayError as exc:
        log_payment(payment, PaymentEvent.Kind.ERROR, (gettext("The gateway is unavailable: %(exc)s") % {"exc": exc}))
        return payment, order, "unknown"
    sec = gateways._registry.secrets_of(gw)
    ctx = _ctx(payment, order)
    kind = PaymentEvent.Kind.INQUIRE if inquire else PaymentEvent.Kind.VERIFY
    try:
        result = gw.inquire_status(ctx) if inquire else gw.verify_transaction(ctx, dict(params or {}))
    except gateways.GatewayError as exc:
        # شبکه/رد درگاه ≠ ناموفق: وضعیت نامعلوم می‌ماند تا استعلام بعدی؛ هرگز بدون پاسخ معتبر «موفق» یا «ناموفق» ثبت نمی‌شود
        Payment.objects.filter(pk=payment.pk).update(last_checked_at=timezone.now())
        log_payment(payment, PaymentEvent.Kind.ERROR, (gettext("The gateway inquiry failed: %(exc)s") % {"exc": exc}), secrets=sec)
        return payment, order, "unknown"
    except Exception:  # noqa: BLE001
        logger.exception(cli_gettext("Unexpected error while verifying payment %s"), payment.reference)
        log_payment(payment, PaymentEvent.Kind.ERROR, gettext("Internal gateway error during verification."))
        return payment, order, "unknown"
    return _apply(payment.pk, result, gw, sec, kind)


def _apply(payment_id, result, gw, sec, kind):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment_id)
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if payment.status != Payment.Status.INITIATED:  # رقیبِ همزمان زودتر نهایی کرد
            log_payment(payment, PaymentEvent.Kind.DUPLICATE, gettext("Concurrent request; the transaction has already been finalized."))
            return payment, order, "duplicate"
        now = timezone.now()
        payment.last_checked_at = now
        raw = {"status": getattr(result.status, "value", str(result.status)), "amount": result.amount, "currency": result.currency, "gateway_ref": result.gateway_ref, "raw": result.raw}
        if result.status == VerifyStatus.PENDING:
            payment.save(update_fields=["last_checked_at"])
            log_payment(payment, kind, gettext("The transaction is not finalized yet."), data=raw, secrets=sec)
            return payment, order, "pending"
        old = payment.status
        if result.status == VerifyStatus.FAILED:
            payment.status, payment.verified_at = Payment.Status.FAILED, now
            payment.save(update_fields=["status", "verified_at", "last_checked_at"])
            log_payment(payment, kind, gettext("The payment gateway reported failure/cancellation."), data=raw, from_status=old, to_status=payment.status, secrets=sec)
            return payment, order, "applied"
        if result.status != VerifyStatus.PAID:  # مقدار ناشناخته: هرگز موفق فرض نمی‌شود
            log_payment(payment, PaymentEvent.Kind.ERROR, gettext("Invalid status from the gateway; ignored."), data=raw, secrets=sec)
            payment.save(update_fields=["last_checked_at"])
            return payment, order, "unknown"
        problems = _check(payment, order, result, gw)
        payment.verified_at = now
        if problems:
            payment.status = Payment.Status.REVIEW
            msg = (gettext("The payment was reported successful by the gateway but has a mismatch (%(problems)s); manual review required.") % {"problems": gettext(", ").join(problems)})
            logger.warning(cli_gettext("Payment mismatch %s: %s"), payment.reference, problems)
        elif order.status != Order.Status.PENDING:
            payment.status = Payment.Status.REVIEW
            msg = (gettext("A successful payment arrived for an order with status “%(status)s”; manual review required.") % {"status": order.get_status_display()})
            logger.warning(cli_gettext("Successful payment for an order that cannot be accepted %s (%s)"), order.number, order.status)
        else:
            payment.status = Payment.Status.SUCCEEDED
            msg = (gettext("The payment was verified by gateway “%(gateway)s”.") % {"gateway": payment.gateway})
        if not payment.gateway_ref and result.gateway_ref:
            payment.gateway_ref = str(result.gateway_ref)[:100]
        try:
            with transaction.atomic():  # savepoint: قیدهای یکتایی دفاع نهایی در برابر ثبت چندباره
                payment.save(update_fields=["status", "verified_at", "gateway_ref", "last_checked_at"])
        except IntegrityError:
            payment.status, payment.gateway_ref = Payment.Status.REVIEW, ""
            payment.save(update_fields=["status", "verified_at", "gateway_ref", "last_checked_at"])
            msg = gettext("A duplicate payment for this order/transaction was detected; manual review required.")
        log_payment(payment, kind, msg, data=raw, from_status=old, to_status=payment.status, secrets=sec)
        if payment.status == Payment.Status.SUCCEEDED:
            old_o = order.status
            order.status, order.paid_at = Order.Status.PAID, now
            order.save(update_fields=["status", "paid_at", "updated_at"])
            record_event(order, OrderEvent.Kind.STATUS, from_status=old_o, to_status=order.status, message=msg)
        else:
            record_event(order, OrderEvent.Kind.PAYMENT, message=msg)
        return payment, order, "applied"


def finalize_payment(reference: str, params=None):
    """بازگشت مشتری از درگاه. ``params`` فقط اشاره است؛ **نتیجه از API رسمی درگاه** می‌آید. idempotent. ← (payment, order)"""
    payment = Payment.objects.select_related("order", "order__site").filter(reference=reference).first()
    if payment is None:
        raise ShopError(gettext("Payment not found."))
    if payment.status == Payment.Status.INITIATED:
        log_payment(payment, PaymentEvent.Kind.CALLBACK, gettext("The customer returned from the gateway."), data={"params": dict(params or {})})
    payment, order, _ = _run(payment, params, inquire=False)
    return payment, order


def inquire_payment(payment):
    """استعلام وضعیت بدون دخالت مشتری (دکمهٔ پنل، کار زمان‌بندی‌شده). ← (payment, order, outcome)"""
    payment = Payment.objects.select_related("order", "order__site").get(pk=payment.pk)
    return _run(payment, None, inquire=True)


def expire_stale_payments(now=None) -> int:
    """تراکنش‌های باز مانده روی سفارش‌های دیگر-در-انتظار-نبوده پس از STALE_PAYMENT_HOURS «ناموفق» می‌شوند (بعد از یک استعلام نهایی)."""
    now = now or timezone.now()
    n = 0
    qs = Payment.objects.filter(status=Payment.Status.INITIATED, created_at__lt=now - timedelta(hours=STALE_PAYMENT_HOURS)).exclude(order__status=Order.Status.PENDING)
    for pid in list(qs.values_list("pk", flat=True)):
        payment = Payment.objects.select_related("order", "order__site").get(pk=pid)
        _, _, outcome = _run(payment, None, inquire=True)
        payment.refresh_from_db()
        if payment.status == Payment.Status.INITIATED:
            with use_language(language_for_order(payment.order)):
                _fail(payment, gettext("The transaction expired with no result."))
            n += 1
    return n


# ------------------------------------------------------------------ وضعیت‌ها
S = Order.Status
TRANSITIONS = {
    S.PENDING: [S.CANCELLED],
    S.PAID: [S.PROCESSING, S.COMPLETED, S.CANCELLED, S.REFUNDED],
    S.PROCESSING: [S.SHIPPED, S.CANCELLED, S.REFUNDED],
    S.SHIPPED: [S.COMPLETED, S.REFUNDED],
    S.COMPLETED: [S.REFUNDED],
}
RESTOCK_FROM = (S.PENDING, S.PAID, S.PROCESSING)  # کالا هنوز نرفته؛ لغو/استرداد موجودی را برمی‌گرداند


def allowed_next(order):
    """وضعیت‌های بعدیِ مجاز. سفارش دارای کالای فیزیکی نمی‌تواند بدون ارسال مستقیم «تکمیل» شود."""
    return [s for s in TRANSITIONS.get(order.status, []) if not (s == S.COMPLETED and order.status == S.PAID and order.needs_shipping)]


@transaction.atomic
def change_status(order, new_status, *, actor=None, note=""):
    """تغییر وضعیت با قفل ردیف، بررسی گذارهای مجاز و ثبت تاریخچه. استرداد فقط **ثبت وضعیت** است؛ برگشت پول از درگاه خودکار نیست."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if new_status not in allowed_next(order):
        raise ShopError(gettext("This status change is not allowed."))
    old = order.status
    if new_status == S.CANCELLED or (new_status == S.REFUNDED and old in RESTOCK_FROM):
        _restore_stock(order)
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    msg = (note or "").strip()[:250]
    if new_status == S.REFUNDED:
        with use_language(language_for_order(order)):  # the history line is shown to the customer too
            msg = ((gettext("The refund was recorded; returning the money to the customer must be done separately. %(msg)s") % {"msg": msg})).strip()
    record_event(order, OrderEvent.Kind.STATUS, from_status=old, to_status=new_status, message=msg, actor=actor)
    return order


def expire_pending(now=None) -> int:
    """سفارش‌های پرداخت‌نشدهٔ از مهلت گذشته را لغو می‌کند؛ **اما پیش از لغو از درگاه استعلام می‌گیرد** تا سفارشِ پرداخت‌شده‌ای که callbackش گم شده لغو نشود.
    اگر استعلام ممکن نباشد (خطای شبکه) لغو تا اجرای بعد (حداکثر STALE_PAYMENT_HOURS) به تعویق می‌افتد."""
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=settings.SHOP_PENDING_ORDER_MINUTES)
    hard_cutoff = cutoff - timedelta(hours=STALE_PAYMENT_HOURS)
    n = 0
    for order_id in list(Order.objects.filter(status=Order.Status.PENDING, created_at__lt=cutoff).values_list("pk", flat=True)):
        unknown = False
        for payment in Payment.objects.filter(order_id=order_id, status=Payment.Status.INITIATED):
            _, _, outcome = inquire_payment(payment)
            unknown = unknown or outcome == "unknown"
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_id)
                if order.status != Order.Status.PENDING:
                    continue  # در همین فاصله پرداخت تأیید شد
                if unknown and order.created_at > hard_cutoff:
                    continue
                _restore_stock(order)
                order.status = Order.Status.CANCELLED
                with use_language(language_for_order(order)):  # the customer sees this line on the order page
                    order.admin_note = ((gettext("%(admin_note)s Automatic cancellation: the payment deadline passed.") % {"admin_note": order.admin_note})).strip()[:500]
                    event_message = gettext("Automatic cancellation: the payment deadline passed.")
                order.save(update_fields=["status", "admin_note", "updated_at"])
                record_event(order, OrderEvent.Kind.STATUS, from_status=S.PENDING, to_status=S.CANCELLED, message=event_message)
                n += 1
        except Order.DoesNotExist:
            continue
    return n


# ------------------------------------------------------------------ دانلود دیجیتال
def claim_download(order, item_id):
    """اجازهٔ یک دانلود را اتمیک می‌گیرد (سقف تعداد) و قلم سفارش را برمی‌گرداند؛ در غیر این صورت ShopError."""
    if not order.is_paid_state:
        raise ShopError(gettext("Download is possible only after a successful payment (and as long as the order is not cancelled/refunded)."))
    item = order.items.select_related("product").filter(pk=item_id, kind=Product.Kind.DIGITAL).first()
    if item is None or item.product is None or not item.product.digital_file:
        raise ShopError(gettext("The file for this item is not available."))
    limit = settings.SHOP_DIGITAL_DOWNLOAD_LIMIT
    if not OrderItem.objects.filter(pk=item.pk, download_count__lt=limit).update(download_count=F("download_count") + 1):
        raise ShopError((gettext("The download limit (%(limit)s times) has been reached; contact the shop.") % {"limit": limit}))
    record_event(order, OrderEvent.Kind.DOWNLOAD, message=(gettext("Download of “%(name)s”.") % {"name": item.name}))
    return item
