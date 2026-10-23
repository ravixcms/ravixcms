"""Messages written for somebody else (timers, commands, callbacks, hooks) use the recipient's language, never the CLI language.

The suite runs with RAVIX_CLI_LANG="fa" and Django's active language "fa"; the tests that matter switch the terminal to English
(``RAVIX_CLI_LANG=""``, active language "en" — what ``apps.core.apps`` does for a management command) and check that the stored or
sent text is still in the recipient's / site's language."""
import re
from datetime import timedelta
from io import StringIO

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.utils import timezone, translation
from django.utils.translation import gettext

from apps.accounts.models import Role, User
from apps.accounts.tests.test_auth import make_user
from apps.audit.models import AuditLog
from apps.content.models import Post, PublishStatus
from apps.core import clilang
from apps.core.models import SiteSettings
from apps.core.recipient import (current_language, language_for_email, language_for_order, language_for_user, site_default_language,
                                 use_language)

PERSIAN = re.compile("[؀-ۿ]")


def say(msgid, lang):
    with translation.override(lang):
        return gettext(msgid)


class TerminalInEnglish:
    """What a management command / timer looks like: terminal text English, Django's gettext activated in English."""

    def setUp(self):
        super().setUp()
        self._s = override_settings(RAVIX_CLI_LANG="", LANGUAGE_CODE="en")
        self._s.enable()
        clilang.reset_cache()
        translation.activate("en")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._s.disable()
        clilang.reset_cache()
        translation.activate("fa")

    def site(self, language="fa", **fields):
        site = SiteSettings.load()
        site.language, site.installed_at = language, timezone.now()
        for k, v in fields.items():
            setattr(site, k, v)
        site.save()
        return site


class HelperTests(TerminalInEnglish, TestCase):
    def test_order_profile_fallback_site_default(self):
        self.site("de")
        user = make_user("lang_user", language="ru")
        plain = make_user("plain_user")
        self.assertEqual(language_for_user(user), "ru")
        self.assertEqual(language_for_user(user, fallback="es"), "ru")  # the profile beats the fallback
        self.assertEqual(language_for_user(plain, fallback="es"), "es")  # then the caller's fallback
        self.assertEqual(language_for_user(plain), "de")  # then the site default, not the terminal's "en"
        self.assertEqual(language_for_user(None), "de")
        self.assertEqual(language_for_email("LANG_USER@example.com"), "ru")  # case-insensitive match on the account's address
        self.assertEqual(language_for_email("nobody@example.com"), "de")
        self.assertEqual(language_for_email(""), "de")
        self.assertEqual(site_default_language(), "de")

    def test_use_language_switches_gettext_and_restores(self):
        self.site("fa")
        before = translation.get_language()
        with use_language():
            self.assertEqual(translation.get_language(), "fa")
            self.assertRegex(gettext("Posts"), PERSIAN)
        with use_language("de"):
            self.assertEqual(gettext("Posts"), say("Posts", "de"))
        self.assertEqual(translation.get_language(), before)

    def test_current_language_is_the_active_one(self):
        with translation.override("pt-br"):
            self.assertEqual(current_language(), "pt-br")


class EmailTests(TerminalInEnglish, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.site("fa", admin_email="owner@example.com", comments_notify_admin=True, site_name="سایت من")
        mail.outbox.clear()

    def subject(self, name, lang, **ctx):
        with translation.override(lang):
            return " ".join(render_to_string(name, ctx).split())

    def test_verification_email_uses_the_profile_language(self):
        from apps.accounts import emailing

        user = make_user("verify_me", language="de")
        request = Client().get("/accounts/login/").wsgi_request
        with translation.override("ru"):  # the person triggering it happens to browse in Russian
            self.assertTrue(emailing.send_verification_email(request, user))
        self.assertEqual(len(mail.outbox), 1)
        ctx = {"user": user, "link": "x", "hours": 1}
        self.assertEqual(mail.outbox[0].subject, self.subject("accounts/email/verify_subject.txt", "de", **ctx))
        self.assertNotRegex(mail.outbox[0].subject + mail.outbox[0].body, PERSIAN)

    def test_verification_email_without_profile_language_uses_the_interface_language_in_use(self):
        from apps.accounts import emailing

        user = make_user("verify_ru")
        request = Client().get("/accounts/login/").wsgi_request
        with translation.override("ru"):
            emailing.send_verification_email(request, user)
        self.assertEqual(mail.outbox[0].subject, self.subject("accounts/email/verify_subject.txt", "ru", user=user, link="x", hours=1))

    def test_password_reset_email_language(self):
        make_user("reset_me", language="de")
        c = Client()
        c.cookies["ravix_lang"] = "ru"  # the visitor browses in Russian but the account says German
        c.post("/accounts/password-reset/", {"email": "reset_me@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, self.subject("accounts/email/password_reset_subject.txt", "de"))
        mail.outbox.clear()
        make_user("reset_plain")  # no profile language: the language the visitor is using
        c.post("/accounts/password-reset/", {"email": "reset_plain@example.com"})
        self.assertEqual(mail.outbox[0].subject, self.subject("accounts/email/password_reset_subject.txt", "ru"))

    def comment(self, ui_language):
        from apps.comments.models import Comment

        post = Post.objects.create(title="نوشته", slug="p1", body="x", status=PublishStatus.PUBLISHED, publish_at=timezone.now() - timedelta(days=1))
        c = Client()
        c.cookies["ravix_lang"] = ui_language
        c.post("/fa/blog/p1/comment/", {"name": "علی", "email": "ali@example.com", "body": "دیدگاه"})
        self.assertTrue(Comment.objects.exists())

    def test_comment_notification_goes_to_the_admin_in_the_site_language(self):
        self.comment("ru")  # the commenter browses in Russian, the admin address has no account → site default (fa)
        self.assertEqual(len(mail.outbox), 1)
        self.assertRegex(mail.outbox[0].subject, PERSIAN)
        self.assertIn("سایت من", mail.outbox[0].subject)
        self.assertNotIn(say("New comment on “%(site_name)s”", "ru")[:8], mail.outbox[0].subject)

    def test_comment_notification_uses_the_admin_accounts_language(self):
        make_user("the_owner", role=Role.ADMIN, language="de")
        User.objects.filter(username="the_owner").update(email="owner@example.com")
        self.comment("ru")
        self.assertTrue(mail.outbox[0].subject.startswith(say("New comment on “%(site_name)s”", "de").split("“")[0].split("„")[0]))


class SendTestEmailTests(TerminalInEnglish, TestCase):
    def run_command(self, *args):
        mail.outbox.clear()
        out = StringIO()
        call_command("send_test_email", *args, stdout=out)
        return out.getvalue()

    def test_default_language_is_the_recipients_then_the_sites(self):
        self.site("fa")
        make_user("tester", language="de")
        User.objects.filter(username="tester").update(email="tester@example.com")
        self.run_command("tester@example.com")
        self.assertEqual(mail.outbox[0].subject, say("Ravix test email", "de"))
        self.run_command("stranger@example.com")
        self.assertEqual(mail.outbox[0].subject, say("Ravix test email", "fa"))

    def test_lang_option_and_terminal_stays_in_the_cli_language(self):
        self.site("fa")
        output = self.run_command("a@example.com", "--lang", "ru")
        self.assertEqual(mail.outbox[0].subject, say("Ravix test email", "ru"))
        self.assertRegex(mail.outbox[0].body, "[а-яА-Я]")
        self.assertNotRegex(output, "[؀-ۿа-яА-Я]")  # the terminal is English whatever the message language
        self.assertIn("message language: ru", output)

    def test_subject_option_and_unknown_language(self):
        self.run_command("a@example.com", "--subject", "My subject", "--lang", "de")
        self.assertEqual(mail.outbox[0].subject, "My subject")
        from django.core.management import CommandError

        with self.assertRaises(CommandError):
            self.run_command("a@example.com", "--lang", "xx")


class PublishScheduledTests(TerminalInEnglish, TestCase):
    def test_audit_message_is_in_the_site_language_not_the_terminals(self):
        self.site("fa")
        Post.objects.create(title="زمان‌بندی", slug="sched", body="x", status=PublishStatus.SCHEDULED, publish_at=timezone.now() - timedelta(minutes=1))
        out = StringIO()
        call_command("publish_scheduled", stdout=out)
        self.assertNotRegex(out.getvalue(), PERSIAN)  # terminal: English
        message = AuditLog.objects.filter(action="publish").latest("pk").message
        self.assertRegex(message, PERSIAN)  # stored text for the staff: site language
        self.assertNotIn("was published", message)


class SlugAuditTests(TerminalInEnglish, TestCase):
    def test_stored_notes_use_the_site_language(self):
        from apps.content.models import Page, Redirect

        self.site("de")
        Page.objects.create(title="x", slug="x", body="x", status="published")
        Page.objects.filter(slug="x").update(slug="fr")  # a language code as slug, as an old site could have
        call_command("ravix_fix_slugs", stdout=StringIO())
        note = Redirect.objects.get(old_path="/fr/").note
        self.assertEqual(note, say("Automatic: the address was a language code", "de")[:200])
        self.assertNotEqual(note, "Automatic: the address was a language code")
        self.assertTrue(AuditLog.objects.latest("pk").message.startswith("Adresse automatisch geändert"))


from apps.shop.tests.helpers import ShopTestCase  # noqa: E402


class ShopOrderLanguageTests(ShopTestCase):
    """Order history lines are shown to the customer: they follow the order's language, not the timer's terminal."""

    def terminal_english(self):
        s = override_settings(RAVIX_CLI_LANG="", LANGUAGE_CODE="en")
        s.enable()
        self.addCleanup(s.disable)
        clilang.reset_cache()
        self.addCleanup(clilang.reset_cache)
        translation.activate("en")
        self.addCleanup(translation.activate, "fa")

    def make_order(self, lang, user=None):
        from apps.shop.models import Order
        from apps.shop.services import create_order

        with translation.override(lang):
            order = create_order(self.site, [{"product": self.product, "qty": 1}], self.checkout_data(), user=user)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=3))
        return Order.objects.get(pk=order.pk)

    def test_language_is_stored_when_the_order_is_placed(self):
        self.assertEqual(self.make_order("de").language, "de")
        self.assertEqual(self.make_order("fa").language, "fa")

    def test_checkout_through_the_site_stores_the_visitors_language(self):
        from apps.shop.models import Order

        c = Client()
        c.post("/de/shop/cart/add/", {"product": self.product.pk, "qty": 1})
        c.post("/de/shop/checkout/", self.checkout_data())
        self.assertEqual(Order.objects.latest("pk").language, "de")

    def test_expire_pending_writes_in_the_order_language(self):
        from apps.shop.services import expire_pending

        order = self.make_order("de")
        self.terminal_english()
        self.assertEqual(expire_pending(), 1)
        order.refresh_from_db()
        event = order.events.latest("pk")
        self.assertEqual(event.message, say("Automatic cancellation: the payment deadline passed.", "de"))
        self.assertNotEqual(event.message, "Automatic cancellation: the payment deadline passed.")
        self.assertIn(event.message, order.admin_note)

    def test_old_orders_fall_back_to_the_profile_then_the_site_language(self):
        from apps.shop.models import Order
        from apps.shop.services import expire_pending

        self.terminal_english()
        SiteSettings.objects.update(language="fa")
        buyer = make_user("buyer", language="ru")
        with_profile = self.make_order("fa", user=buyer)
        without = self.make_order("fa")
        Order.objects.filter(pk__in=[with_profile.pk, without.pk]).update(language="")  # created before the field existed
        self.assertEqual(language_for_order(Order.objects.get(pk=with_profile.pk)), "ru")
        self.assertEqual(language_for_order(Order.objects.get(pk=without.pk)), "fa")
        expire_pending()
        message = "Automatic cancellation: the payment deadline passed."
        self.assertEqual(with_profile.events.latest("pk").message, say(message, "ru"))
        self.assertEqual(without.events.latest("pk").message, say(message, "fa"))

    def test_refund_note_uses_the_order_language(self):
        from apps.shop.models import Order
        from apps.shop.services import change_status

        order = self.make_order("de")
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID)
        self.terminal_english()
        change_status(order, Order.Status.REFUNDED, actor=self.admin)
        msg = order.events.latest("pk").message
        self.assertTrue(msg.startswith(say("The refund was recorded; returning the money to the customer must be done separately. %(msg)s", "de")[:20]))
