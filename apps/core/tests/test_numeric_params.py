"""ورودی‌های عددی (query/POST) با اعداد غول‌پیکر، رقم‌های غیر ASCII و مقدارهای مخرب هرگز ۵۰۰ نمی‌دهند (مانع 1-6)."""
from django.test import SimpleTestCase, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.core.params import MAX_ID, safe_id, safe_ids, safe_int
from apps.comments.models import Comment
from apps.content.models import Post, PublishStatus

from apps.accounts.models import Role
from apps.updater.tests.helpers import make_user

BAD = ["9" * 30, str(MAX_ID + 1), "²", "١٢٣", "-5", "1e9", "0", "", "%00", "x" * 50, "1.5"]
PARAMS = ["post", "category", "tag", "author", "page", "ids", "offset", "version", "reply", "id", "product", "qty", "status", "q", "parent", "per_page", "page_size"]


class HelperTests(SimpleTestCase):
    def test_safe_int_and_id(self):
        self.assertEqual(safe_id("12"), 12)
        for bad in ("9" * 30, str(MAX_ID + 1), "²", "١٢٣", "-5", "0", "", None, "1.5", " "):
            self.assertIsNone(safe_id(bad), bad)
        self.assertEqual(safe_int("0", 5), 0)
        self.assertEqual(safe_int("x", 5), 5)
        self.assertEqual(safe_ids(["1", "bad", "9" * 30, "3"]), [1, 3])


def _plain_routes(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            if p.pattern.regex.groups:
                continue
            yield from _plain_routes(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern) and not p.pattern.regex.groups:
            yield "/" + (prefix + str(p.pattern)).lstrip("^").rstrip("$")


class NoServerErrorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("fuzz_root", Role.SUPER_ADMIN)
        cls.post = Post.objects.create(title="t", slug="t", author=cls.admin, status=PublishStatus.PUBLISHED)

    def setUp(self):
        self.client.force_login(self.admin)
        self.client.raise_request_exception = False

    def test_panel_and_public_get_with_hostile_numbers(self):
        routes = sorted({r for r in _plain_routes(get_resolver().url_patterns) if "<" not in r and "(" not in r and "?" not in r})
        self.assertGreater(len(routes), 40)
        skip = ("logout", "/install/", "download", "export", "/media/", "/static/")
        failures = []
        for url in routes:
            if any(s in url for s in skip):
                continue
            for param in PARAMS:
                for bad in (BAD[0], BAD[2], BAD[4]):
                    r = self.client.get(url, {param: bad})
                    if r.status_code >= 500:
                        failures.append(f"{url}?{param}={bad[:12]} → {r.status_code}")
        self.assertEqual(failures[:10], [])

    def test_overflow_in_django_admin_filter_is_400_not_500(self):
        r = self.client.get("/django-admin/content/post/", {"author": "9" * 30})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "درخواست نامعتبر", status_code=400)

    def test_comment_bulk_and_filters(self):
        url = reverse("comments:list")
        for bad in BAD:
            self.assertLess(self.client.get(url, {"post": bad}).status_code, 500, bad)
        bulk = reverse("comments:bulk")
        for bad in BAD:
            r = self.client.post(bulk, {"ids": [bad, "9" * 30], "action": "approve"})
            self.assertLess(r.status_code, 500, bad)

    def test_posts_filters(self):
        url = reverse("content:post_list")
        for param in ("category", "tag", "author"):
            for bad in BAD:
                self.assertLess(self.client.get(url, {param: bad}).status_code, 500, (param, bad))

    def test_public_reply_param(self):
        for bad in BAD:
            r = self.client.get(reverse("public:post_detail", args=[self.post.slug]), {"reply": bad})
            self.assertEqual(r.status_code, 200, bad)
