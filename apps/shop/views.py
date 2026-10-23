"""صفحات عمومی فروشگاه. همهٔ داده‌ها محدود به سایتی است که از دامنهٔ درخواست تشخیص داده می‌شود."""
from django.utils.translation import gettext
import hashlib
from urllib.parse import urlsplit
from urllib.parse import urlencode

from django.conf import settings
from apps.core.params import safe_id, safe_int
from django.core.cache import cache
from django.core.paginator import Paginator
from django.contrib.auth.views import redirect_to_login
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect
from django.templatetags.static import static
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views import View

from apps.public.dto import PageItem
from apps.public.rendering import render_page
from apps.public.seo import absolute_url, clip, make_seo
from apps.public.views import _crumbs
from apps.sites.middleware import current_site

from . import gateways, services
from .forms import CheckoutForm
from .models import Order, Payment, Product, ProductCategory, SandboxTransaction


class ShopView(View):
    """پایه: سایت را از دامنه می‌گیرد؛ دامنهٔ ناشناخته یا فروشگاه خاموش ← ۴۰۴."""

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.site = current_site(request)

    def dispatch(self, request, *args, **kwargs):
        if self.site is None or not settings.SHOP_ENABLED:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def render(self, request, template, ctx, *, title, description="", robots="", status=200, jsonld=None, og_image="", path=None, crumbs=()):
        ctx = {**ctx, "currency": settings.SHOP_CURRENCY_LABEL, "site_obj": self.site, "cart_count": sum(services.cart_raw(request, self.site).values()) if hasattr(request, "session") else 0}
        body = mark_safe('<link rel="stylesheet" href="' + static("shop/shop.css") + '"><div class="container shop">'
                         + render_to_string(f"shop/public/{template}", ctx, request=request) + "</div>")
        item = PageItem(title=title, url=request.path, body_html=body, date_display="", date_iso="", builder=True, show_title=True)
        breadcrumbs = _crumbs((gettext("Home"), "/"), (gettext("Shop"), reverse("shop:list")), *crumbs, (title, "")) if crumbs is not None else _crumbs((gettext("Home"), "/"), (title, ""))

        def seo(site):
            return make_seo(request, site, title=title, description=description, path=path or request.path, robots=robots, og_image=og_image,
                            og_type="website", jsonld=jsonld)

        response = render_page(request, "page_detail.html", {"page": item, "breadcrumbs": breadcrumbs}, status=status, seo_factory=seo)
        response["Cache-Control"] = "private, no-store"  # صفحه توکن CSRF/نشست دارد؛ نباید در کش عمومی بماند
        return response


def _limited(request, bucket, limit, window):
    from apps.core.clientip import client_ip

    ip = client_ip(request) or ""
    key = "shop-rl:" + hashlib.sha256(f"{bucket}|{ip}".encode()).hexdigest()[:32]
    if cache.add(key, 1, window):
        return False
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, window)
        return False


def gateway_choices(site):
    out = []
    for name in gateways.enabled_gateway_names(site):
        try:
            gateways.get_gateway(name, site=site)
        except gateways.GatewayError:
            continue
        out.append((name, gateways.available_gateways()[name].label or name))
    return out


def gateway_response(view, request, redirect):
    """هدایت مشتری به درگاه: GET ← ۳۰۲ ؛ POST ← صفحهٔ فرم خودکار (CSP فقط برای همان میزبان مجازِ درگاه باز می‌شود)."""
    if redirect.method == "GET":
        resp = HttpResponseRedirect(redirect.url)
        resp["Cache-Control"] = "private, no-store"
        return resp
    resp = view.render(request, "gateway_redirect.html", {"redirect": redirect}, title=gettext("Redirect to payment gateway"), robots="noindex,nofollow", crumbs=())
    host = urlsplit(redirect.url).hostname
    if host:
        resp["Content-Security-Policy"] = resp["Content-Security-Policy"].replace("form-action 'self'", f"form-action 'self' https://{host}")
    return resp


class ProductListView(ShopView):
    def get(self, request):
        qs = Product.objects.filter(site=self.site, is_active=True).select_related("category")
        q, cat, kind, sort = (request.GET.get(k, "") for k in ("q", "category", "kind", "sort"))
        qs = services.search_products(qs, q=q, category=cat, kind=kind, sort=sort)
        page = Paginator(qs, settings.SHOP_PAGE_SIZE).get_page(request.GET.get("page"))
        categories = list(ProductCategory.objects.filter(site=self.site))
        filtered = bool(q.strip() or cat or kind or sort)
        suffix = (gettext(" — page %(number)s") % {"number": page.number}) if page.number > 1 else ""
        return self.render(request, "list.html", {"page": page, "categories": categories, "q": q[:80], "cat": cat, "kind": kind, "sort": sort,
                                                  "qs": urlencode({k: v for k, v in (("q", q[:80]), ("category", cat), ("kind", kind), ("sort", sort)) if v})},
                           title=(gettext("Shop%(suffix)s") % {"suffix": suffix}), description=gettext("Shop product list"), crumbs=None, robots="noindex,follow" if filtered else "",
                           path=reverse("shop:list") + (f"?page={page.number}" if page.number > 1 and not filtered else ""))


class ProductDetailView(ShopView):
    def get(self, request, slug):
        p = get_object_or_404(Product.objects.select_related("category"), site=self.site, slug=slug, is_active=True)
        gallery = list(p.gallery.all())
        img = absolute_url(request, p.image.url) if p.image else ""
        ld = {"@context": "https://schema.org", "@type": "Product", "name": p.name, "description": clip(p.description, 300),
              "offers": {"@type": "Offer", "price": str(p.final_price), "priceCurrency": "IRT" if settings.SHOP_CURRENCY_LABEL == "تومان" else "IRR",
                         "availability": "https://schema.org/InStock" if p.in_stock else "https://schema.org/OutOfStock",
                         "url": absolute_url(request, p.get_absolute_url())}}
        if img:
            ld["image"] = [img]
        return self.render(request, "detail.html", {"p": p, "gallery": gallery}, title=p.name, description=p.description or p.name, jsonld=[ld],
                           og_image=p.image.url if p.image else "", path=p.get_absolute_url())


class CartView(ShopView):
    def get(self, request):
        lines, total = services.cart_lines(request, self.site)
        return self.render(request, "cart.html", {"lines": lines, "total": total, "summary": services.cart_summary(lines)}, title=gettext("Cart"), robots="noindex,follow")


class CartAddView(ShopView):
    http_method_names = ["post"]

    def post(self, request):
        try:
            pid, qty = safe_id(request.POST.get("product", "")), safe_int(request.POST.get("qty", "1"), 1, hi=10**6)
        except ValueError:
            raise Http404
        if pid is None:
            raise Http404
        p = get_object_or_404(Product, pk=pid, site=self.site, is_active=True)  # محصول سایت دیگر ← ۴۰۴
        current = int(services.cart_raw(request, self.site).get(str(p.pk), 0))
        try:
            services.cart_set(request, self.site, p.pk, 1 if p.is_digital else max(1, min(current + max(qty, 1), services.MAX_QTY)))
        except services.ShopError:
            pass
        return redirect("shop:cart")


class CartUpdateView(ShopView):
    http_method_names = ["post"]

    def post(self, request):
        for key, value in request.POST.items():
            if key.startswith("qty_") and safe_id(key[4:]) is not None:
                try:
                    qty = max(-1, min(int(value), 10**6))
                except ValueError:
                    continue
                if Product.objects.filter(pk=safe_id(key[4:]), site=self.site, is_active=True).exists() or qty <= 0:
                    services.cart_set(request, self.site, safe_id(key[4:]), qty)
        return redirect("shop:cart")


class CheckoutView(ShopView):
    def _guard(self, request):
        try:
            if not gateways.enabled_gateway_names(self.site):
                gateways.get_gateway(None, site=self.site)  # پیام دقیق (مثلاً آزمایشی در تولید) را می‌دهد
                return gettext("The payment gateway is not active; placing orders is not possible right now.")
            gateways.get_gateway(None, site=self.site)
        except gateways.GatewayError as exc:
            return str(exc)
        return ""

    def _choices(self):
        return gateway_choices(self.site)

    def _guest_block(self, request):
        if not request.user.is_authenticated and not settings.SHOP_ALLOW_GUEST_CHECKOUT:
            return gettext("Please sign in before placing an order.")
        return ""

    def get(self, request):
        lines, total = services.cart_lines(request, self.site)
        if not lines:
            return redirect("shop:cart")
        initial = {}
        if request.user.is_authenticated:
            initial["customer_name"] = request.user.display_name
            initial["email"] = request.user.email
        error = self._guard(request) or self._guest_block(request)
        return self.render(request, "checkout.html", {"form": CheckoutForm(initial=initial, needs_shipping=services.cart_summary(lines)["needs_shipping"], gateway_choices=self._choices()), "lines": lines, "total": total,
                                                      "summary": services.cart_summary(lines), "gateway_error": error},
                           title=gettext("Order placed"), robots="noindex,follow")

    def post(self, request):
        lines, total = services.cart_lines(request, self.site)
        if not lines:
            return redirect("shop:cart")
        summary = services.cart_summary(lines)
        form, error = CheckoutForm(request.POST, needs_shipping=summary["needs_shipping"], gateway_choices=self._choices()), self._guard(request) or self._guest_block(request)
        if not error and _limited(request, "checkout", 10, 3600):
            error = gettext("You have made too many attempts; try again a little later.")
        if not error and form.is_valid():
            try:
                order = services.create_order(self.site, lines, {k: v for k, v in form.cleaned_data.items() if k != "gateway"}, request.user)
            except services.StockError as exc:
                error = str(exc)
            else:
                try:
                    redirect_to = services.start_payment(order, request, form.cleaned_data.get("gateway") or None)
                except services.ShopError as exc:
                    services.change_status(order, Order.Status.CANCELLED, note=gettext("Starting the payment failed."))
                    error = str(exc)
                else:
                    services.cart_clear(request, self.site)
                    return gateway_response(self, request, redirect_to)
        return self.render(request, "checkout.html", {"form": form, "lines": lines, "total": total, "summary": summary, "gateway_error": error},
                           title=gettext("Order placed"), robots="noindex,follow", status=400)


def _order_or_404(site, number, token):
    order = Order.objects.filter(site=site, number=number).first()
    if order is None or not __import__("hmac").compare_digest(order.token, token):
        raise Http404
    return order


class OrderView(ShopView):
    def get(self, request, number, token):
        order = _order_or_404(self.site, number, token)
        return self.render(request, "order.html", {"order": order, "items": order.items.all(), "can_pay": order.status == Order.Status.PENDING,
                                                 "can_download": order.is_paid_state, "gateway_choices": gateway_choices(self.site) if order.status == Order.Status.PENDING else [], "events": order.events.exclude(kind="note")},
                           title=(gettext("Order %(number)s") % {"number": order.number}), robots="noindex,nofollow", crumbs=())


class OrderPayView(ShopView):
    http_method_names = ["post"]

    def post(self, request, number, token):
        order = _order_or_404(self.site, number, token)
        try:
            return gateway_response(self, request, services.start_payment(order, request, request.POST.get("gateway") or None))
        except services.ShopError:
            return redirect(order.get_public_url())


@method_decorator(csrf_exempt, name="dispatch")  # بانک‌ها گاهی با POST از دامنهٔ دیگر برمی‌گردانند؛ امنیت از تأیید سرور-به-سرور است نه از این درخواست
class PaymentCallbackView(ShopView):
    """بازگشت از درگاه. هیچ پارامتر مرورگری باور نمی‌شود؛ finalize_payment از API رسمی درگاه می‌پرسد."""

    http_method_names = ["get", "post"]

    def _handle(self, request, reference):
        payment = Payment.objects.select_related("order").filter(reference=reference, order__site=self.site).first()
        if payment is None:
            raise Http404
        if _limited(request, "pay-callback", 60, 600):
            return HttpResponse(status=429)
        params = {**request.GET.dict(), **request.POST.dict()} if request.method == "POST" else request.GET.dict()
        try:
            _p, order = services.finalize_payment(reference, params)
        except services.ShopError:
            raise Http404
        return redirect(order.get_public_url())

    def get(self, request, reference):
        return self._handle(request, reference)

    def post(self, request, reference):
        return self._handle(request, reference)


class SandboxPayView(ShopView):
    """صفحهٔ بانک آزمایشی؛ فقط وقتی درگاه آزمایشی مجاز است."""

    def _tx(self, reference):
        if not gateways.sandbox_allowed():
            raise Http404
        payment = Payment.objects.select_related("order").filter(reference=reference, order__site=self.site, gateway="sandbox").first()
        tx = SandboxTransaction.objects.filter(reference=reference).first()
        if payment is None or tx is None:
            raise Http404
        return payment, tx

    def get(self, request, reference):
        payment, tx = self._tx(reference)
        return self.render(request, "sandbox.html", {"payment": payment, "tx": tx}, title=gettext("Test payment gateway"), robots="noindex,nofollow", crumbs=())

    def post(self, request, reference):
        payment, tx = self._tx(reference)
        if tx.state == SandboxTransaction.State.PENDING:
            tx.state = SandboxTransaction.State.PAID if request.POST.get("action") == "pay" else SandboxTransaction.State.FAILED
            tx.save(update_fields=["state"])
        return redirect(reverse("shop:callback", args=[reference]))


class DownloadView(ShopView):
    """دانلود فایل دیجیتال: فقط با توکن سفارش، فقط برای سفارش پرداخت‌شده، با سقف تعداد؛ فایل از مسیر خصوصی خوانده می‌شود."""

    def get(self, request, number, token, item_id):
        order = _order_or_404(self.site, number, token)
        try:
            item = services.claim_download(order, item_id)
        except services.ShopError:
            raise Http404
        f = item.product.digital_file
        try:
            handle = f.open("rb")
        except OSError:
            raise Http404
        resp = FileResponse(handle, as_attachment=True, filename=f.name.rsplit("/", 1)[-1])
        resp["Cache-Control"] = "private, no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


class MyOrdersView(ShopView):
    """فهرست سفارش‌های کاربر واردشده. سفارش مهمان به حساب مرتبط نمی‌شود مگر هنگام ثبت وارد شده باشد."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        orders = Order.objects.filter(site=self.site, user=request.user).order_by("-created_at", "-id")[:100]
        return self.render(request, "my_orders.html", {"orders": orders}, title=gettext("My orders"), robots="noindex,nofollow", crumbs=())
