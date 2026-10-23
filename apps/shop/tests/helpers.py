import re

from django.test import Client, TestCase, override_settings

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.shop.models import Product
from apps.sites.models import Site, SiteDomain, SiteMember


@override_settings(SHOP_ALLOW_SANDBOX_IN_PRODUCTION=True)  # اجرای تست با DEBUG=False است؛ درگاه آزمایشی صراحتاً مجاز می‌شود
class ShopTestCase(TestCase):
    def setUp(self):
        import shutil
        import tempfile

        from django.core.cache import cache

        # فایل‌های دیجیتال و رسانهٔ تست هرگز در پوشهٔ واقعی پروژه نمی‌مانند
        tmp = tempfile.mkdtemp(prefix="ravix-shop-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        priv = override_settings(SHOP_PRIVATE_ROOT=tmp + "/private", MEDIA_ROOT=tmp + "/media")
        priv.enable()
        self.addCleanup(priv.disable)
        cache.clear()
        self.site = Site.objects.get(is_default=True)
        self.admin = make_user("boss", Role.ADMIN)
        self.superadmin = make_user("root", Role.SUPER_ADMIN)
        self.author = make_user("writer", Role.AUTHOR)
        self.product = Product.objects.create(site=self.site, name="کتاب آزمایشی", slug="book", price=150000, stock=5, description="توضیح")

    def add_to_cart(self, client, product=None, qty=1):
        return client.post("/fa/shop/cart/add/", {"product": (product or self.product).pk, "qty": qty})

    def checkout_data(self, **over):
        d = {"customer_name": "علی رضایی", "phone": "09123456789", "email": "a@b.com", "address": "تهران، خیابان آزادی، پلاک ۱", "note": ""}
        d.update(over)
        return d

    def place_order(self, client=None, product=None, qty=1):
        c = client or Client()
        self.add_to_cart(c, product, qty)
        r = c.post("/fa/shop/checkout/", self.checkout_data())
        return c, r

    def pay(self, client, response, action="pay"):
        """از redirect درگاه آزمایشی تا بازگشت؛ ref را برمی‌گرداند."""
        assert response.status_code == 302, response.content[:300]
        url = response["Location"]
        ref = re.search(r"/shop/pay/sandbox/([0-9a-f]+)/", url).group(1)
        r = client.post(url, {"action": action})
        page = client.get(r["Location"], follow=True)  # callback → صفحهٔ سفارش
        return ref, page
