"""دیدگاه‌ها: ثبت عمومی، ضداسپم، پاک‌سازی، پاسخ تودرتو، مدیریت پنل و عملیات گروهی، اطلاع به مدیر، hookها، قالب پیش‌فرض."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.comments.models import Comment
from apps.content.models import Post, PublishStatus
from apps.core.models import SiteSettings
from apps.plugins import hooks
from apps.plugins.tests.helpers import PluginTestCase, base_manifest

C = Comment.Status


def make_post(slug="p1", **kw):
    d = dict(title="نوشته", slug=slug, body="متن", status=PublishStatus.PUBLISHED, publish_at=timezone.now() - timedelta(days=1))
    d.update(kw)
    return Post.objects.create(**d)


def data(**kw):
    d = {"name": "علی", "email": "ali@example.com", "body": "دیدگاه من"}
    d.update(kw)
    return d


def make_comment(post, status=C.APPROVED, parent=None, **kw):
    d = dict(post=post, parent=parent, depth=(parent.depth + 1) if parent else 0, author_name="x", author_email="x@example.com",
             body="b", body_html="b", status=status)
    d.update(kw)
    return Comment.objects.create(**d)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.post = make_post()
        self.url = reverse("public:comment_submit", args=["p1"])
        self.page = reverse("public:post_detail", args=["p1"])

    def send(self, **kw):
        return self.client.post(self.url, data(**kw))


class SubmitTests(Base):
    def test_pending_by_default_and_hidden_until_approved(self):
        r = self.send()
        self.assertEqual((r.status_code, r["Location"]), (302, f"{self.page}?cm=pending#comments"))
        c = Comment.objects.get()
        self.assertEqual((c.status, c.depth, c.post_id), (C.PENDING, 0, self.post.pk))
        page = self.client.get(self.page).content.decode()
        self.assertNotIn("دیدگاه من", page)
        self.assertIn("پس از تأیید مدیر", self.client.get(self.page + "?cm=pending").content.decode())
        Comment.objects.update(status=C.APPROVED)
        self.assertIn("دیدگاه من", self.client.get(self.page).content.decode())

    def test_published_immediately_when_approval_off(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"comments_require_approval": False})
        r = self.send()
        c = Comment.objects.get()
        self.assertEqual((c.status, r["Location"]), (C.APPROVED, f"{self.page}?cm=ok#comment-{c.pk}"))
        self.assertIn("دیدگاه من", self.client.get(self.page).content.decode())

    def test_email_and_ip_never_public(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"comments_require_approval": False})
        self.send(email="secret-mail@example.com")
        self.assertNotIn("secret-mail", self.client.get(self.page).content.decode())

    def test_works_without_csrf_token(self):
        r = Client(enforce_csrf_checks=True).post(self.url, data())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Comment.objects.count(), 1)

    def test_foreign_origin_rejected(self):
        r = self.client.post(self.url, data(), HTTP_ORIGIN="https://evil.example")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Comment.objects.exists())
        self.assertEqual(self.client.post(self.url, data(), HTTP_ORIGIN="http://testserver").status_code, 302)

    def test_get_not_allowed_and_draft_or_unknown_post_404(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        make_post("d", status=PublishStatus.DRAFT, publish_at=None)
        self.assertEqual(self.client.post(reverse("public:comment_submit", args=["d"]), data()).status_code, 404)
        self.assertEqual(self.client.post(reverse("public:comment_submit", args=["nope"]), data()).status_code, 404)

    def test_validation_errors_rerender_form_with_values(self):
        r = self.send(name="ا", email="bad", body="  ")
        self.assertEqual(r.status_code, 400)
        html = r.content.decode()
        for msg in ("نام را وارد کنید", "ایمیل معتبر", "متن دیدگاه را بنویسید"):
            self.assertIn(msg, html)
        self.assertFalse(Comment.objects.exists())
        r = self.send(email="bad", name='"><b>x</b>')
        self.assertNotIn('"><b>x</b>', r.content.decode())  # مقدار برگردانده‌شده escape می‌شود

    def test_too_long_body(self):
        with override_settings(RAVIN_COMMENT_MAX_LENGTH=10):
            self.assertEqual(self.send(body="x" * 11).status_code, 400)

    def test_duplicate_rejected(self):
        self.send()
        r = self.send()
        self.assertEqual(r.status_code, 400)
        self.assertIn("قبلاً ثبت شده", r.content.decode())
        self.assertEqual(Comment.objects.count(), 1)

    def test_closed_post_rejects_but_shows_existing(self):
        make_comment(self.post, body_html="قدیمی")
        Post.objects.update(comments_open=False)
        r = self.send()
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Comment.objects.filter(body="دیدگاه من").exists())
        page = self.client.get(self.page).content.decode()
        self.assertIn("بسته شده است", page)
        self.assertIn("قدیمی", page)
        self.assertNotIn('name="body"', page)


class AntiSpamTests(Base):
    def test_honeypot_discards_silently_with_same_response(self):
        r = self.send(website="http://spam.example")
        self.assertEqual((r.status_code, r["Location"]), (302, f"{self.page}?cm=pending#comments"))
        self.assertFalse(Comment.objects.exists())

    def test_page_contains_hidden_honeypot(self):
        page = self.client.get(self.page).content.decode()
        self.assertRegex(page, r'name="website"[^>]*tabindex="-1"')

    @override_settings(RAVIN_COMMENT_LIMIT_PER_IP=3)
    def test_rate_limit_per_ip(self):
        for i in range(3):
            self.assertEqual(self.send(body=f"متن {i}").status_code, 302)
        r = self.send(body="متن دیگر")
        self.assertEqual(r.status_code, 429)
        self.assertIn("زیاد است", r.content.decode())
        self.assertEqual(Comment.objects.count(), 3)

    @override_settings(RAVIN_COMMENT_LIMIT_PER_IP=1)
    def test_rate_limit_counts_honeypot_and_invalid_attempts_not(self):
        self.assertEqual(self.send(website="x").status_code, 302)  # honeypot پیش از شمارش رد می‌شود
        self.assertEqual(self.send().status_code, 302)
        self.assertEqual(self.send(body="دوم").status_code, 429)

    def test_blocked_words_mark_spam_with_pending_looking_response(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"comments_blocked_words": "کازینو\n  Viagra \n"})
        for kw in ({"body": "بهترین کازینو آنلاین"}, {"body": "buy VIAGRA now"}, {"name": "کازینو ۲۴"}):
            r = self.send(**kw)
            self.assertEqual(r["Location"], f"{self.page}?cm=pending#comments")
        self.assertEqual(Comment.objects.filter(status=C.SPAM).count(), 3)
        self.assertEqual(self.send(body="متن سالم").status_code, 302)
        self.assertEqual(Comment.objects.filter(status=C.PENDING).count(), 1)

    def test_blocked_word_beats_auto_approval(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"comments_require_approval": False, "comments_blocked_words": "bad"})
        self.send(body="very bad")
        self.assertEqual(Comment.objects.get().status, C.SPAM)

    def test_spam_never_displayed(self):
        make_comment(self.post, status=C.SPAM, body_html="اسپم‌اسپم")
        make_comment(self.post, status=C.TRASH, body_html="زباله‌زباله")
        page = self.client.get(self.page).content.decode()
        self.assertNotIn("اسپم‌اسپم", page)
        self.assertNotIn("زباله‌زباله", page)


class SanitizeTests(Base):
    def stored(self, body):
        self.send(body=body)
        return Comment.objects.get().body_html

    def test_script_and_handlers_removed(self):
        html = self.stored('<script>alert(1)</script>سلام <img src=x onerror=alert(2)> <b onclick="x()">پررنگ</b>')
        for bad in ("<script", "alert(1)", "<img", "onerror", "onclick"):
            self.assertNotIn(bad, html)
        self.assertIn("<b>پررنگ</b>", html)

    def test_dangerous_links_stripped_and_safe_links_nofollow(self):
        html = self.stored('<a href="javascript:alert(1)">بد</a> <a href="https://example.com/x">خوب</a> <a href="data:text/html,x">داده</a>')
        self.assertNotIn("javascript:", html)
        self.assertNotIn("data:", html)
        self.assertRegex(html, r'<a href="https://example.com/x" rel="[^"]*nofollow[^"]*ugc[^"]*">خوب</a>')

    def test_style_iframe_attributes_removed(self):
        html = self.stored('<p style="x:y" class="c">سلام</p><iframe src="//e.com"></iframe><form action=x><input></form>')
        for bad in ("style=", "class=", "iframe", "<form", "<input"):
            self.assertNotIn(bad, html)

    def test_newlines_become_br(self):
        self.assertIn("یک<br>دو", self.stored("یک\nدو"))

    def test_html_only_comment_rejected(self):
        self.assertEqual(self.send(body="<script>x</script>").status_code, 400)

    def test_raw_markup_in_name_is_escaped_on_page(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"comments_require_approval": False})
        self.send(name="<b>x</b>نام")
        page = self.client.get(self.page).content.decode()
        self.assertNotIn("<b>x</b>نام", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;نام", page)


class ThreadTests(Base):
    def test_depth_limits(self):
        top = make_comment(self.post)
        self.assertEqual(self.send(parent=top.pk, body="پاسخ ۱").status_code, 302)
        r1 = Comment.objects.get(body="پاسخ ۱")
        self.assertEqual((r1.depth, r1.parent_id), (1, top.pk))
        r1.status = C.APPROVED
        r1.save()
        self.send(parent=r1.pk, body="پاسخ ۲")
        r2 = Comment.objects.get(body="پاسخ ۲")
        self.assertEqual(r2.depth, 2)
        r2.status = C.APPROVED
        r2.save()
        r = self.send(parent=r2.pk, body="پاسخ ۳")
        self.assertEqual(r.status_code, 400)
        self.assertIn("امکان پاسخ", r.content.decode())
        self.assertFalse(Comment.objects.filter(body="پاسخ ۳").exists())

    def test_cannot_reply_to_unapproved_other_post_or_garbage(self):
        pending = make_comment(self.post, status=C.PENDING)
        other = make_comment(make_post("p2"))
        for parent in (pending.pk, other.pk, 99999, "abc"):
            self.assertEqual(self.send(parent=parent, body=f"x{parent}").status_code, 400, parent)
        self.assertEqual(Comment.objects.count(), 2)

    def test_tree_rendered_nested_with_reply_links_up_to_depth(self):
        top = make_comment(self.post, body_html="سطح-صفر")
        mid = make_comment(self.post, parent=top, body_html="سطح-یک")
        low = make_comment(self.post, parent=mid, body_html="سطح-دو")
        make_comment(self.post, parent=top, status=C.PENDING, body_html="در-انتظار")
        page = self.client.get(self.page).content.decode()
        self.assertTrue(page.index("سطح-صفر") < page.index("سطح-یک") < page.index("سطح-دو"))
        self.assertNotIn("در-انتظار", page)
        self.assertIn(f"?reply={top.pk}#comment-form", page)
        self.assertIn(f"?reply={mid.pk}#comment-form", page)
        self.assertNotIn(f"?reply={low.pk}#", page)  # عمق ۲ پاسخ ندارد
        self.assertEqual(page.count('class="comment"'), 3)

    def test_reply_param_prefills_parent(self):
        top = make_comment(self.post, author_name="مریم")
        page = self.client.get(self.page + f"?reply={top.pk}").content.decode()
        self.assertIn(f'name="parent" value="{top.pk}"', page)
        self.assertIn("پاسخ به مریم", page)
        self.assertNotIn('name="parent"', self.client.get(self.page + "?reply=99999").content.decode())

    def test_deleting_parent_or_post_cascades(self):
        top = make_comment(self.post)
        make_comment(self.post, parent=top)
        top.delete()
        self.assertFalse(Comment.objects.exists())
        make_comment(self.post)
        self.post.delete()
        self.assertFalse(Comment.objects.exists())

    def test_count_only_approved(self):
        make_comment(self.post)
        make_comment(self.post, status=C.PENDING)
        self.assertIn("(1)", self.client.get(self.page).content.decode())


class NotifyTests(Base):
    def setUp(self):
        super().setUp()
        SiteSettings.objects.update_or_create(pk=1, defaults={"admin_email": "boss@example.com", "site_name": "راویکس"})

    def test_admin_gets_email_for_pending(self):
        self.send()
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["boss@example.com"])
        self.assertIn("دیدگاه جدید", m.subject)
        self.assertIn("منتظر تأیید", m.body)
        self.assertIn("/panel/comments/?status=pending", m.body)
        self.assertNotIn("ali@example.com", m.body)  # ایمیل بازدیدکننده در ایمیل نمی‌آید

    def test_no_email_for_spam_honeypot_or_when_disabled_or_unset(self):
        SiteSettings.objects.update(comments_blocked_words="bad")
        self.send(body="bad")
        self.send(website="x", body="دیگر")
        self.assertEqual(len(mail.outbox), 0)
        SiteSettings.objects.update(comments_blocked_words="", comments_notify_admin=False)
        self.send(body="سوم")
        SiteSettings.objects.update(comments_notify_admin=True, admin_email="")
        self.send(body="چهارم")
        self.assertEqual(len(mail.outbox), 0)

    def test_email_html_stripped_and_smtp_failure_does_not_reject_comment(self):
        with mock.patch("django.core.mail.message.EmailMessage.send", side_effect=OSError("smtp down")):
            with self.assertLogs("apps.comments.notify", level="ERROR"):
                r = self.send(body="<b>سلام</b>")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Comment.objects.count(), 1)


class PanelTests(TestCase):
    def setUp(self):
        self.post = make_post()
        self.admin = make_user("adm", Role.ADMIN)
        self.author = make_user("aut", Role.AUTHOR)
        self.url = reverse("comments:list")
        self.bulk = reverse("comments:bulk")
        self.pending = make_comment(self.post, C.PENDING, body_html="در-انتظار", author_name="نفر۱")
        self.approved = make_comment(self.post, C.APPROVED, body_html="تأیید-شده", author_name="نفر۲")
        self.spam = make_comment(self.post, C.SPAM, body_html="اسپم-اسپم")
        self.trash = make_comment(self.post, C.TRASH, body_html="زباله-زباله")

    def test_access_control(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.bulk, {"action": "approve", "ids": [self.pending.pk]}).status_code, 302)
        self.client.force_login(self.author)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.bulk, {"action": "approve", "ids": [self.pending.pk]}).status_code, 403)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, C.PENDING)
        self.assertTrue(self.admin.has_cap("comments.moderate"))
        self.assertFalse(self.author.has_cap("comments.moderate"))

    def test_bulk_requires_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.bulk).status_code, 405)

    def test_default_list_hides_spam_and_trash_and_tabs_filter(self):
        self.client.force_login(self.admin)
        html = self.client.get(self.url).content.decode()
        self.assertIn("در-انتظار", html)
        self.assertIn("تأیید-شده", html)
        self.assertNotIn("اسپم-اسپم", html)
        self.assertNotIn("زباله-زباله", html)
        self.assertIn("اسپم-اسپم", self.client.get(self.url, {"status": "spam"}).content.decode())
        self.assertIn("زباله-زباله", self.client.get(self.url, {"status": "trash"}).content.decode())
        only = self.client.get(self.url, {"status": "pending"}).content.decode()
        self.assertIn("در-انتظار", only)
        self.assertNotIn("تأیید-شده", only)

    def test_search_and_post_filter(self):
        other = make_comment(make_post("p2"), C.APPROVED, body_html="دیگری-دیگری", author_name="ناشناس")
        self.client.force_login(self.admin)
        html = self.client.get(self.url, {"q": "نفر۲"}).content.decode()
        self.assertIn("تأیید-شده", html)
        self.assertNotIn("در-انتظار", html)
        html = self.client.get(self.url, {"post": other.post_id}).content.decode()
        self.assertIn("دیگری-دیگری", html)
        self.assertNotIn("تأیید-شده", html)

    def do(self, action, *comments, **extra):
        self.client.force_login(self.admin)
        return self.client.post(self.bulk, {"action": action, "ids": [c.pk for c in comments], **extra})

    def test_bulk_status_changes_with_audit(self):
        self.do("approve", self.pending, self.spam)
        self.assertEqual(set(Comment.objects.filter(pk__in=[self.pending.pk, self.spam.pk]).values_list("status", flat=True)), {C.APPROVED})
        self.do("spam", self.approved)
        self.do("trash", self.pending)
        self.do("pending", self.trash)
        self.approved.refresh_from_db(); self.pending.refresh_from_db(); self.trash.refresh_from_db()
        self.assertEqual((self.approved.status, self.pending.status, self.trash.status), (C.SPAM, C.TRASH, C.PENDING))
        self.assertTrue(AuditLog.objects.filter(object_type="دیدگاه", action="update", message__contains="2 دیدگاه").exists())

    def test_bulk_without_selection_or_with_bad_action(self):
        r = self.do("approve")
        self.assertEqual(r.status_code, 302)
        self.do("explode", self.pending)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, C.PENDING)

    def test_permanent_delete_only_spam_and_trash(self):
        self.do("delete", self.approved, self.pending, self.spam, self.trash)
        self.assertEqual(set(Comment.objects.values_list("pk", flat=True)), {self.approved.pk, self.pending.pk})
        self.assertTrue(AuditLog.objects.filter(action="delete", object_type="دیدگاه").exists())

    def test_empty_trash(self):
        self.do("empty_trash")
        self.assertFalse(Comment.objects.filter(status=C.TRASH).exists())
        self.assertEqual(Comment.objects.count(), 3)

    def test_next_param_cannot_redirect_off_site(self):
        for nxt in ("https://evil.example/", "//evil.example/", "/panel/comments//evil.example", "/other/"):
            r = self.do("approve", self.pending, next=nxt)
            self.assertEqual(r["Location"], self.url, nxt)
        self.assertEqual(self.do("approve", self.pending, next=self.url + "?status=spam")["Location"], self.url + "?status=spam")

    def test_nav_item_visible_to_admin(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard:home")), "دیدگاه‌ها")

    def test_body_rendered_from_sanitized_html_only(self):
        make_comment(self.post, C.PENDING, body="<script>alert(1)</script>", body_html="امن")
        self.client.force_login(self.admin)
        self.assertNotIn("<script>alert(1)", self.client.get(self.url).content.decode())


class SettingsAndPostFormTests(TestCase):
    def test_site_settings_and_post_form_fields(self):
        sa = make_user("sa", Role.SUPER_ADMIN)
        self.client.force_login(sa)
        url = reverse("core:site_settings")
        self.assertContains(self.client.get(url), "کلمات مسدود در دیدگاه")
        r = self.client.post(url, {"site_name": "س", "comments_notify_admin": "on", "comments_blocked_words": "الف\nب"})
        self.assertEqual(r.status_code, 302)
        cfg = SiteSettings.load()
        self.assertEqual((cfg.comments_require_approval, cfg.comments_notify_admin, cfg.comments_blocked_words), (False, True, "الف\nب"))
        self.assertTrue(SiteSettings._meta.get_field("comments_require_approval").default)
        r = self.client.post(reverse("content:post_create"), {"title": "ن", "slug": "", "excerpt": "", "body": "م", "status": "published", "author": sa.pk})
        self.assertFalse(Post.objects.get().comments_open)  # تیک نخورده = بسته
        self.assertContains(self.client.get(reverse("content:post_create")), "دیدگاه‌ها باز باشد")
        self.assertTrue(Post._meta.get_field("comments_open").default)


class HookTests(PluginTestCase):
    VIEWS = '''from django.http import HttpResponse

CALLS = []


def home(request, api):
    return HttpResponse("x")


def to_spam(data, api, **ctx):
    return {"status": "spam", "body": "HACKED", "author_name": "HACKED"}


def auto_approve(data, api, **ctx):
    return {"status": "approved"}


def crash(data, api, **ctx):
    raise RuntimeError("hook-boom")


def created(api, **ctx):
    from apps.comments.models import Comment
    Comment.objects.filter(pk=ctx["comment_id"]).update(author_name="seen-" + ctx["status"])


def changed(api, **ctx):
    from apps.comments.models import Comment
    Comment.objects.filter(pk=ctx["comment_id"]).update(body_html=ctx["old"] + ">" + ctx["new"])
'''

    def setUp(self):
        super().setUp()
        cache.clear()
        self.post = make_post()
        self.url = reverse("public:comment_submit", args=["p1"])

    def make(self, slug, **hook_map):
        m = base_manifest(slug, public_routes=[], hooks=[{"name": n, "handler": h} for n, h in hook_map.items()])
        self.write_plugin(slug, manifest=m, views=self.VIEWS)
        self.install_activate(slug)

    def test_before_save_can_mark_spam_but_not_edit_or_approve(self):
        self.make("spamfilter", **{"comments.before_save": "views:to_spam"})
        Client().post(self.url, data())
        c = Comment.objects.get()
        self.assertEqual((c.status, c.body, c.author_name), (C.SPAM, "دیدگاه من", "علی"))

    def test_before_save_cannot_auto_approve(self):
        self.make("approver", **{"comments.before_save": "views:auto_approve"})
        Client().post(self.url, data())
        self.assertEqual(Comment.objects.get().status, C.PENDING)

    def test_crashing_hook_does_not_break_submission(self):
        self.make("crashy", **{"comments.before_save": "views:crash"})
        r = Client().post(self.url, data())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Comment.objects.get().status, C.PENDING)

    def test_created_and_status_changed_actions(self):
        self.make("watcher", **{"comments.created": "views:created", "comments.status_changed": "views:changed"})
        Client().post(self.url, data())
        c = Comment.objects.get()
        self.assertEqual(c.author_name, "seen-pending")
        self.login(self.admin)
        self.client.post(reverse("comments:bulk"), {"action": "approve", "ids": [c.pk]})
        c.refresh_from_db()
        self.assertEqual(c.body_html, "pending>approved")
        self.client.post(reverse("comments:bulk"), {"action": "approve", "ids": [c.pk]})  # بدون تغییر: hook دوباره اجرا نمی‌شود
        c.refresh_from_db()
        self.assertEqual(c.body_html, "pending>approved")

    def test_sanitize_comment_unit(self):
        base = {"status": "pending", "body": "x"}
        self.assertEqual(hooks.sanitize_comment(base, {"status": "spam"})["status"], "spam")
        for bad in ("approved", "trash", 5, None):
            self.assertEqual(hooks.sanitize_comment(base, {"status": bad})["status"], "pending")
        self.assertEqual(hooks.sanitize_comment(base, "nope"), base)
        self.assertEqual(hooks.sanitize_comment(base, {"body": "y"})["body"], "x")

    def test_hooks_are_registered(self):
        from apps.plugins.hooks_registry import HOOKS

        self.assertEqual(HOOKS["comments.before_save"], "filter")
        self.assertEqual((HOOKS["comments.created"], HOOKS["comments.status_changed"]), ("action", "action"))
