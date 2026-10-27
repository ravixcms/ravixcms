"""API فروشگاه (فقط‌خواندنی، نسخهٔ ۱). کاتالوگ: عمومیِ سایتِ دامنه. سفارش‌ها: کارکنان با مجوز shop.orders فقط برای سایت فعال پنل."""
from django.db.models import Q
from django.urls import path
from rest_framework import serializers
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from . import gateways

from apps.sites.access import can_access_site, panel_site
from apps.sites.middleware import current_site

from .models import Order, OrderEvent, OrderItem, Payment, Product, ProductCategory, ShopSettings


class Paginator(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductCategory
        fields = ["id", "name", "slug"]


class ProductSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    category = CategorySerializer(read_only=True)
    final_price = serializers.IntegerField(read_only=True)
    discount_amount = serializers.IntegerField(read_only=True)
    in_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "kind", "category", "description", "price", "discount_percent", "discount_amount", "final_price",
                  "in_stock", "stock", "image", "images", "url"]

    def to_representation(self, obj):
        data = super().to_representation(obj)
        cache = self.context.setdefault("_shop_currency", {})
        if obj.site_id not in cache:
            cache[obj.site_id] = ShopSettings.for_site(obj.site).currency
        data["currency"] = cache[obj.site_id]  # ISO-like code; amounts are integers in its base unit (IRR rial, IRT toman, USD/EUR cents)
        if not obj.track_stock:
            data["stock"] = None  # موجودی نامحدود
        return data

    def get_url(self, obj):
        return self.context["request"].build_absolute_uri(obj.get_absolute_url())

    def get_image(self, obj):
        return self.context["request"].build_absolute_uri(obj.image.url) if obj.image else None

    def get_images(self, obj):
        req = self.context["request"]
        return [{"url": req.build_absolute_uri(i.image.url), "alt": i.alt} for i in obj.gallery.all()]


def _catalog(request):
    site = current_site(request)
    return Product.objects.filter(site=site, is_active=True) if site else Product.objects.none()


class GatewayListAPI(APIView):
    """درگاه‌های فعال برای نمایش در صفحهٔ پرداخت (فقط نام و برچسب؛ هرگز تنظیمات)."""

    permission_classes = [AllowAny]

    def get(self, request):
        site = current_site(request)
        if site is None:
            return Response([])
        out = []
        for name in gateways.enabled_gateway_names(site):
            try:
                gateways.get_gateway(name, site=site)
            except gateways.GatewayError:
                continue
            cls = gateways.available_gateways()[name]
            out.append({"name": name, "label": cls.label or name, "is_test": cls.is_test})
        return Response(out)


class CategoryListAPI(ListAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        site = current_site(self.request)
        return ProductCategory.objects.filter(site=site) if site else ProductCategory.objects.none()


class ProductListAPI(ListAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = ProductSerializer
    pagination_class = Paginator

    def get_queryset(self):
        from .services import search_products

        p = self.request.query_params
        return search_products(_catalog(self.request).select_related("category").prefetch_related("gallery"), q=p.get("q", ""), category=p.get("category", ""),
                               kind=p.get("kind", ""), sort=p.get("sort", ""))


class ProductDetailAPI(RetrieveAPIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    serializer_class = ProductSerializer
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return _catalog(self.request).select_related("category").prefetch_related("gallery")


class CanViewOrders(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.has_cap("shop.orders") and can_access_site(u, panel_site(request)))


class ItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["name", "kind", "list_price", "unit_discount", "unit_price", "quantity", "line_total"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["gateway", "amount", "currency", "status", "is_test", "created_at", "verified_at"]  # شناسه‌های ارائه‌دهنده و هر داده‌ٔ اتصال عمداً نیست


class EventSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = OrderEvent
        fields = ["kind", "from_status", "to_status", "message", "actor", "created_at"]

    def get_actor(self, obj):
        return obj.actor.username if obj.actor_id else None


class OrderSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Order
        fields = ["id", "number", "status", "status_label", "customer_name", "phone", "subtotal", "discount_total", "total", "currency", "needs_shipping", "created_at", "paid_at"]


class OrderDetailSerializer(OrderSerializer):
    items = ItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    events = EventSerializer(many=True, read_only=True)
    user = serializers.SerializerMethodField()

    class Meta(OrderSerializer.Meta):
        fields = OrderSerializer.Meta.fields + ["email", "address", "note", "admin_note", "user", "items", "payments", "events"]

    def get_user(self, obj):
        return obj.user.username if obj.user_id else None  # None = خرید مهمان


class OrderListAPI(ListAPIView):
    permission_classes = [CanViewOrders]
    serializer_class = OrderSerializer
    pagination_class = Paginator

    def get_queryset(self):
        qs = Order.objects.filter(site=panel_site(self.request))
        status = self.request.query_params.get("status", "")
        if status in Order.Status.values:
            qs = qs.filter(status=status)
        q = self.request.query_params.get("q", "").strip()[:50]
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(customer_name__icontains=q) | Q(phone__icontains=q))
        return qs


class OrderDetailAPI(RetrieveAPIView):
    permission_classes = [CanViewOrders]
    serializer_class = OrderDetailSerializer
    lookup_field = "number"

    def get_queryset(self):
        return Order.objects.filter(site=panel_site(self.request)).select_related("user").prefetch_related("items", "payments", "events__actor")


urlpatterns = [
    path("shop/gateways/", GatewayListAPI.as_view(), name="shop-gateways"),
    path("shop/categories/", CategoryListAPI.as_view(), name="shop-categories"),
    path("shop/products/", ProductListAPI.as_view(), name="shop-products"),
    path("shop/products/<str:slug>/", ProductDetailAPI.as_view(), name="shop-product"),
    path("shop/orders/", OrderListAPI.as_view(), name="shop-orders"),
    path("shop/orders/<str:number>/", OrderDetailAPI.as_view(), name="shop-order"),
]
