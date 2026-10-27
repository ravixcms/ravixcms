"""پنل مدیریت فروشگاه. هر View: ورود + قابلیت نقش + دسترسی به «سایت فعال»؛ و همهٔ کوئری‌ها با site فیلتر می‌شوند."""
from django.utils.translation import gettext
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.sites.access import can_access_site, panel_site

from . import services
from . import gateways
from .forms import AttributeForm, BaseVariantFormSet, CategoryForm, GatewayConfigForm, ProductForm, ProductImageForm, ShopSettingsForm, VariantForm
from .models import GatewayConfig, Payment, ShopSettings
from .payments import registry, vault
from .models import AttributeValue, Order, OrderEvent, Product, ProductAttribute, ProductCategory, ProductImage, ProductVariant


class SiteScopedMixin(CapabilityRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if self.required_capability and not request.user.has_cap(self.required_capability):
            raise PermissionDenied
        self.site = panel_site(request)
        if self.site is None or not can_access_site(request.user, self.site):
            raise PermissionDenied
        return View.dispatch(self, request, *args, **kwargs)


class ProductsAdmin(SiteScopedMixin):
    required_capability = "shop.manage"


class OrdersAdmin(SiteScopedMixin):
    required_capability = "shop.orders"


class GatewaysAdmin(SiteScopedMixin):
    required_capability = "shop.gateways"


class ProductListView(ProductsAdmin, View):
    def get(self, request):
        from django.db.models import Count, F, Q, Value
        from django.db.models.functions import Coalesce

        q = request.GET.get("q", "").strip()[:100]
        stock = request.GET.get("stock", "")
        threshold = ShopSettings.for_site(self.site).low_stock_threshold
        qs = Product.objects.filter(site=self.site).annotate(variant_count=Count("variants", distinct=True))
        limit = Coalesce("low_stock_threshold", Value(threshold))
        simple_low = Q(track_stock=True, stock__gt=0, stock__lte=limit) & ~Q(kind=Product.Kind.VARIABLE)
        simple_out = Q(track_stock=True, stock__lte=0) & ~Q(kind=Product.Kind.VARIABLE)
        v_low = Q(kind=Product.Kind.VARIABLE, variants__is_active=True, variants__track_stock=True, variants__stock__gt=0, variants__stock__lte=limit)
        v_out = Q(kind=Product.Kind.VARIABLE, variants__is_active=True, variants__track_stock=True, variants__stock__lte=0)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__iexact=q) | Q(variants__sku__iexact=q)).distinct()
        if stock == "low":
            qs = qs.filter(simple_low | v_low).distinct()
        elif stock == "out":
            qs = qs.filter(simple_out | v_out).distinct()
        page = Paginator(qs.order_by("-created_at", "-pk"), 20).get_page(request.GET.get("page"))
        for p in page:  # badges on the page only (one settings lookup above, not one per row)
            p.low_flag = p.track_stock and not p.is_variable and 0 < p.stock <= (p.low_stock_threshold if p.low_stock_threshold is not None else threshold)
            p.out_flag = p.track_stock and not p.is_variable and p.stock <= 0
        low_total = Product.objects.filter(site=self.site).filter(simple_low | v_low).distinct().count()
        qd = "&".join(f"{k}={v}" for k, v in (("q", q), ("stock", stock)) if v)
        return render(request, "shop/panel/products.html", {"page_obj": page, "paginator": page.paginator, "is_paginated": page.has_other_pages(),
                                                           "q": q, "stock": stock, "low_total": low_total, "query_string": qd, "site_obj": self.site})


def _variant_formset(product, data=None, files=None):
    from django.forms import modelformset_factory

    fs = modelformset_factory(ProductVariant, form=VariantForm, formset=BaseVariantFormSet, extra=0, can_delete=True)
    return fs(data, files, queryset=ProductVariant.objects.filter(product=product, site=product.site).prefetch_related("options"), form_kwargs={"product": product}, prefix="variants")


def _product_context(obj, form, request=None, variant_formset=None):
    ctx = {"form": form, "obj": obj, "image_form": ProductImageForm(), "gallery": obj.gallery.all() if obj else []}
    if obj is not None and obj.is_variable:
        ctx["variant_formset"] = variant_formset or _variant_formset(obj)
    return ctx


class ProductEditView(ProductsAdmin, View):
    def _obj(self, pk):
        return get_object_or_404(Product, pk=pk, site=self.site) if pk else None

    def get(self, request, pk=None):
        obj = self._obj(pk)
        return render(request, "shop/panel/product_form.html", _product_context(obj, ProductForm(instance=obj, site=self.site)))

    def post(self, request, pk=None):
        obj = self._obj(pk)
        form = ProductForm(request.POST, request.FILES, instance=obj, site=self.site)
        if form.is_valid():
            saved = form.save()
            log_event(request, AuditLog.Action.UPDATE if obj else AuditLog.Action.CREATE, saved, (gettext("Product “%(name)s” was saved") % {"name": saved.name}))
            messages.success(request, gettext("Product saved."))
            if saved.is_variable:  # variants are managed on the product page
                return redirect("shop_panel:product_edit", pk=saved.pk)
            return redirect("shop_panel:products")
        return render(request, "shop/panel/product_form.html", _product_context(obj, form), status=400)


class VariantSaveView(ProductsAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk, site=self.site, kind=Product.Kind.VARIABLE)
        fs = _variant_formset(product, request.POST, request.FILES)
        if not fs.is_valid():
            return render(request, "shop/panel/product_form.html", _product_context(product, ProductForm(instance=product, site=self.site), variant_formset=fs), status=400)
        fs.save()
        product.sync_variable_price()
        log_event(request, AuditLog.Action.UPDATE, product, (gettext("Variants of “%(name)s” were saved") % {"name": product.name}))
        messages.success(request, gettext("Variants saved."))
        return redirect("shop_panel:product_edit", pk=product.pk)


class VariantGenerateView(ProductsAdmin, View):
    """Create the variants for every combination of the product's attributes that does not exist yet."""

    http_method_names = ["post"]
    LIMIT = 100

    def post(self, request, pk):
        from itertools import product as cartesian

        product = get_object_or_404(Product, pk=pk, site=self.site, kind=Product.Kind.VARIABLE)
        groups = [list(a.values.all()) for a in product.variation_attributes.order_by("name") if a.values.exists()]
        if not groups:
            messages.error(request, gettext("Choose attributes that have values first, then save the product."))
            return redirect("shop_panel:product_edit", pk=pk)
        combos = [c for c in cartesian(*groups)]
        existing = set(product.variants.values_list("options_key", flat=True))
        todo = [c for c in combos if ProductVariant.key_for([v.pk for v in c]) not in existing]
        if len(existing) + len(todo) > self.LIMIT:
            messages.error(request, gettext("That would create more than %(limit)s variants; use fewer attributes or values.") % {"limit": self.LIMIT})
            return redirect("shop_panel:product_edit", pk=pk)
        from django.db import transaction

        with transaction.atomic():
            for combo in todo:
                variant = ProductVariant(product=product, price=product.price, options_key=ProductVariant.key_for([v.pk for v in combo]))
                variant.save()
                variant.options.set(combo)
        product.sync_variable_price()
        log_event(request, AuditLog.Action.UPDATE, product, (gettext("Variants of “%(name)s” were generated") % {"name": product.name}))
        messages.success(request, gettext("%(count)s variant(s) were created.") % {"count": len(todo)})
        return redirect("shop_panel:product_edit", pk=pk)


class ProductDeleteView(ProductsAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        obj = get_object_or_404(Product, pk=pk, site=self.site)
        name = obj.name
        obj.delete()  # آیتم‌های سفارش با نام و قیمت لحظهٔ خرید حفظ می‌شوند
        log_event(request, AuditLog.Action.DELETE, None, (gettext("Product “%(name)s” was deleted") % {"name": name}), object_type=gettext("Product"))
        messages.success(request, gettext("Product deleted."))
        return redirect("shop_panel:products")


class OrderListView(OrdersAdmin, View):
    def get(self, request):
        status = request.GET.get("status", "")
        qs = Order.objects.filter(site=self.site).select_related("user")
        if status in Order.Status.values:
            qs = qs.filter(status=status)
        who = request.GET.get("who", "")
        if who == "guest":
            qs = qs.filter(user__isnull=True)
        elif who == "user":
            qs = qs.filter(user__isnull=False)
        q = request.GET.get("q", "").strip()[:50]
        if q:
            from django.db.models import Q

            qs = qs.filter(Q(number__icontains=q) | Q(customer_name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
        page = Paginator(qs, 20).get_page(request.GET.get("page"))
        qsd = "&".join(f"{k}={v}" for k, v in (("status", status), ("q", q), ("who", who)) if v)
        revenue = Order.objects.filter(site=self.site, status__in=[Order.Status.PAID, Order.Status.PROCESSING, Order.Status.SHIPPED, Order.Status.COMPLETED]).aggregate(s=Sum("total"))["s"] or 0
        return render(request, "shop/panel/orders.html", {"page_obj": page, "paginator": page.paginator, "is_paginated": page.has_other_pages(), "query_string": qsd,
                                                         "statuses": Order.Status.choices, "status": status, "who": who, "q": q, "revenue": revenue, "site_obj": self.site})


class OrderDetailView(OrdersAdmin, View):
    def _order(self, pk):
        return get_object_or_404(Order.objects.select_related("user").prefetch_related("items", "payments__events"), pk=pk, site=self.site)  # سفارش سایت دیگر ← ۴۰۴

    def get(self, request, pk):
        order = self._order(pk)
        nxt = [(s.value, s.label) for s in services.allowed_next(order)]
        return render(request, "shop/panel/order.html", {"order": order, "next_statuses": nxt, "events": order.events.select_related("actor")})

    def post(self, request, pk):
        order = self._order(pk)
        note = request.POST.get("note", "").strip()[:250]
        if request.POST.get("action") == "note":
            if note:
                services.record_event(order, OrderEvent.Kind.NOTE, message=note, actor=request.user)
                messages.success(request, gettext("Note recorded."))
            return redirect("shop_panel:order", pk=pk)
        try:
            services.change_status(order, request.POST.get("status", ""), actor=request.user, note=note)
        except services.ShopError as exc:
            messages.error(request, str(exc))
        else:
            log_event(request, AuditLog.Action.UPDATE, order, (gettext("The status of order %(number)s changed: %(POST)s") % {"number": order.number, "POST": request.POST.get('status')}))
            messages.success(request, gettext("The order status was updated."))
        return redirect("shop_panel:order", pk=pk)


class CategoryListView(ProductsAdmin, View):
    def get(self, request):
        return render(request, "shop/panel/categories.html", {"categories": services.category_tree(self.site), "site_obj": self.site})


class CategoryEditView(ProductsAdmin, View):
    def _obj(self, pk):
        return get_object_or_404(ProductCategory, pk=pk, site=self.site) if pk else None

    def get(self, request, pk=None):
        obj = self._obj(pk)
        return render(request, "shop/panel/category_form.html", {"form": CategoryForm(instance=obj, site=self.site), "obj": obj})

    def post(self, request, pk=None):
        obj = self._obj(pk)
        form = CategoryForm(request.POST, instance=obj, site=self.site)
        if form.is_valid():
            saved = form.save()
            log_event(request, AuditLog.Action.UPDATE if obj else AuditLog.Action.CREATE, saved, (gettext("Category “%(name)s” was saved") % {"name": saved.name}))
            messages.success(request, gettext("Category saved."))
            return redirect("shop_panel:categories")
        return render(request, "shop/panel/category_form.html", {"form": form, "obj": obj}, status=400)


class CategoryDeleteView(ProductsAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        obj = get_object_or_404(ProductCategory, pk=pk, site=self.site)
        name = obj.name
        obj.delete()  # محصولات بدون دسته می‌مانند (SET_NULL)
        log_event(request, AuditLog.Action.DELETE, None, (gettext("Category “%(name)s” was deleted") % {"name": name}), object_type=gettext("Product category"))
        messages.success(request, gettext("Category deleted."))
        return redirect("shop_panel:categories")


class GalleryAddView(ProductsAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk, site=self.site)
        form = ProductImageForm(request.POST, request.FILES)
        if form.is_valid():
            img = form.save(commit=False)
            img.product = product
            img.save()
            messages.success(request, gettext("The image was added to the gallery."))
        else:
            messages.error(request, gettext("The image is not valid."))
        return redirect("shop_panel:product_edit", pk=pk)


class GalleryDeleteView(ProductsAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk, image_id):
        product = get_object_or_404(Product, pk=pk, site=self.site)
        get_object_or_404(ProductImage, pk=image_id, product=product).delete()
        messages.success(request, gettext("The image was deleted."))
        return redirect("shop_panel:product_edit", pk=pk)


# ------------------------------------------------------------------ درگاه‌های پرداخت
def _gateway_cls_or_404(name):
    cls = gateways.available_gateways().get(name)
    if cls is None:
        raise Http404
    return cls


def _cannot_enable_reason(cls, cfg):
    """دلیل ممنوعیت فعال‌سازی یا ''. فعال‌کردن درگاه ناقص/آزمایشیِ تولید مجاز نیست."""
    if cls.is_test and not registry.sandbox_allowed():
        return gettext("The test gateway is not allowed in production.")
    missing = registry.missing_fields(cls, cfg)
    if missing:
        return (gettext("The settings are incomplete: %(items)s") % {"items": gettext(", ").join(f.label for f in missing)})
    return ""


def _gateway_state(site, name, cls):
    row = registry.get_config_row(site, name)
    try:
        cfg = registry.load_config(cls, row)
        error = ""
    except vault.SecretsError as exc:
        cfg, error = {}, str(exc)
    return {"name": name, "label": cls.label or name, "description": cls.description, "is_test": cls.is_test, "enabled": registry.is_enabled(site, name),
            "test_mode": row.test_mode if row else True, "configured": not registry.missing_fields(cls, cfg) and not error, "error": error,
            "builtin": name in registry.GATEWAYS, "row": row}


class GatewayListView(GatewaysAdmin, View):
    def get(self, request):
        items = [_gateway_state(self.site, n, c) for n, c in sorted(gateways.available_gateways().items())]
        return render(request, "shop/panel/gateways.html", {"items": items, "site_obj": self.site, "key_configured": vault.key_configured(),
                                                           "sandbox_allowed": registry.sandbox_allowed()})


class GatewayEditView(GatewaysAdmin, View):
    def _setup(self, name):
        cls = _gateway_cls_or_404(name)
        row = registry.get_config_row(self.site, name)
        try:
            stored = vault.decrypt_json(row.secrets_encrypted) if row else {}
            self.vault_error = ""
        except vault.SecretsError as exc:
            stored, self.vault_error = {}, str(exc)
        return cls, row, stored

    def _form(self, cls, row, stored, data=None):
        has_secret = {f.key: bool(stored.get(f.key)) for f in cls.config_fields if f.secret}
        env_keys = [f.key for f in cls.config_fields if registry.env_override(cls.name, f.key)]
        initial = {"enabled": row.enabled if row else False, "test_mode": row.test_mode if row else True}
        for f in cls.config_fields:
            if not f.secret:
                initial[f"cfg_{f.key}"] = (row.public_config.get(f.key, f.default) if row else f.default)
        return GatewayConfigForm(data, initial=initial, gateway_cls=cls, has_secret=has_secret, env_keys=env_keys)

    def get(self, request, name):
        cls, row, stored = self._setup(name)
        return render(request, "shop/panel/gateway_form.html", {"form": self._form(cls, row, stored), "cls": cls, "row": row, "vault_error": self.vault_error})

    def post(self, request, name):
        cls, row, stored = self._setup(name)
        form = self._form(cls, row, stored, request.POST)
        if self.vault_error:
            form.add_error(None, self.vault_error)
        if form.is_valid() and not self.vault_error:
            public, secret = {}, dict(stored)
            for f in cls.config_fields:
                value = form.cleaned_data.get(f"cfg_{f.key}")
                if f.secret:
                    if form.cleaned_data.get(f"clear_{f.key}"):
                        secret.pop(f.key, None)
                    elif value:
                        secret[f.key] = value
                else:
                    public[f.key] = bool(value) if f.type == "bool" else (value or "")
            merged = {**public, **secret}
            for f in cls.config_fields:
                ov = registry.env_override(name, f.key)
                if ov is not None:
                    merged[f.key] = ov
            want_enabled = form.cleaned_data["enabled"]
            problems = [m for m in cls.validate_config(merged)]
            if want_enabled:
                reason = _cannot_enable_reason(cls, merged)
                if reason:
                    problems.append(reason)
                if secret and not vault.key_configured() and not (settings.DEBUG or settings.RAVIX_SECRETS_ALLOW_DERIVED_KEY):
                    problems.append(gettext("The encryption key RAVIX_SECRETS_KEY is not set."))
            if problems:
                for m in problems:
                    form.add_error(None, m)
            else:
                try:
                    blob = vault.encrypt_json(secret)
                except vault.SecretsError as exc:
                    form.add_error(None, str(exc))
                else:
                    row = row or GatewayConfig(site=self.site, gateway=name)
                    row.public_config, row.secrets_encrypted = public, blob
                    row.enabled, row.test_mode, row.updated_by = want_enabled, form.cleaned_data["test_mode"], request.user
                    row.save()
                    log_event(request, AuditLog.Action.UPDATE, None, (gettext("Settings for gateway “%(name)s” were saved (enabled: %(value)s)") % {"name": name, "value": (gettext("yes") if want_enabled else gettext("no"))}),
                              object_type=gettext("Payment gateway"))
                    messages.success(request, gettext("Gateway settings saved."))
                    return redirect("shop_panel:gateways")
        return render(request, "shop/panel/gateway_form.html", {"form": form, "cls": cls, "row": row, "vault_error": self.vault_error}, status=400)


class GatewayToggleView(GatewaysAdmin, View):
    http_method_names = ["post"]

    def post(self, request, name):
        cls = _gateway_cls_or_404(name)
        row = registry.get_config_row(self.site, name)
        enable = request.POST.get("enable") == "1"
        if enable:
            try:
                cfg = registry.load_config(cls, row)
            except vault.SecretsError as exc:
                messages.error(request, str(exc))
                return redirect("shop_panel:gateways")
            reason = _cannot_enable_reason(cls, cfg)
            if reason:
                messages.error(request, reason)
                return redirect("shop_panel:gateways")
        row = row or GatewayConfig(site=self.site, gateway=name)
        row.enabled, row.updated_by = enable, request.user
        row.save()
        log_event(request, AuditLog.Action.UPDATE, None, (gettext("Gateway “%(name)s” was %(value)s") % {"name": name, "value": (gettext("enabled") if enable else gettext("disabled"))}), object_type=gettext("Payment gateway"))
        messages.success(request, gettext("The gateway was enabled.") if enable else gettext("The gateway was disabled; open transactions are still verified."))
        return redirect("shop_panel:gateways")


class PaymentInquireView(OrdersAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk, payment_id):
        order = get_object_or_404(Order, pk=pk, site=self.site)
        payment = get_object_or_404(Payment, pk=payment_id, order=order)
        _p, _o, outcome = services.inquire_payment(payment)
        text = {"applied": gettext("The status was fetched from the gateway and recorded."), "pending": gettext("The gateway says the transaction is not finalized yet."), "duplicate": gettext("This transaction has already been finalized."),
                "unknown": gettext("The inquiry failed (gateway connection error); try again later.")}[outcome]
        (messages.warning if outcome == "unknown" else messages.success)(request, text)
        log_event(request, AuditLog.Action.UPDATE, order, (gettext("Payment status inquiry %(reference)s for order %(number)s: %(outcome)s") % {"reference": payment.reference[:8], "number": order.number, "outcome": outcome}))
        return redirect("shop_panel:order", pk=pk)


class ShopSettingsView(ProductsAdmin, View):
    def _render(self, request, form, status=200):
        return render(request, "shop/panel/settings.html", {"form": form, "site_obj": self.site}, status=status)

    def get(self, request):
        return self._render(request, ShopSettingsForm(instance=ShopSettings.for_site(self.site), site=self.site))

    def post(self, request):
        before = ShopSettings.for_site(self.site)
        old = (before.currency, before.show_in_toman)
        form = ShopSettingsForm(request.POST, instance=before, site=self.site)
        if not form.is_valid():
            return self._render(request, form, 400)
        saved = form.save()
        log_event(request, AuditLog.Action.UPDATE, saved, (gettext("Shop settings changed: currency %(old)s → %(new)s") % {"old": old[0], "new": saved.currency}))
        messages.success(request, gettext("Shop settings were saved."))
        return redirect("shop_panel:settings")


class AttributeListView(ProductsAdmin, View):
    def get(self, request):
        return render(request, "shop/panel/attributes.html", {"attributes": ProductAttribute.objects.filter(site=self.site).prefetch_related("values"), "site_obj": self.site})


class AttributeEditView(ProductsAdmin, View):
    def _obj(self, pk):
        return get_object_or_404(ProductAttribute, pk=pk, site=self.site) if pk else None

    def get(self, request, pk=None):
        obj = self._obj(pk)
        return render(request, "shop/panel/attribute_form.html", {"form": AttributeForm(site=self.site, instance=obj), "obj": obj})

    def post(self, request, pk=None):
        obj = self._obj(pk)
        form = AttributeForm(request.POST, site=self.site, instance=obj)
        if form.is_valid():
            saved = form.save()
            log_event(request, AuditLog.Action.UPDATE if obj else AuditLog.Action.CREATE, saved, (gettext("Attribute “%(name)s” was saved") % {"name": saved.name}))
            messages.success(request, gettext("Attribute saved."))
            return redirect("shop_panel:attributes")
        return render(request, "shop/panel/attribute_form.html", {"form": form, "obj": obj}, status=400)


class AttributeDeleteView(ProductsAdmin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        obj = get_object_or_404(ProductAttribute, pk=pk, site=self.site)
        if ProductVariant.objects.filter(options__attribute=obj).exists():
            messages.error(request, gettext("This attribute is used by variants and cannot be deleted."))
            return redirect("shop_panel:attributes")
        name = obj.name
        obj.delete()
        log_event(request, AuditLog.Action.DELETE, None, (gettext("Attribute “%(name)s” was deleted") % {"name": name}), object_type=gettext("Product attribute"))
        messages.success(request, gettext("Attribute deleted."))
        return redirect("shop_panel:attributes")
