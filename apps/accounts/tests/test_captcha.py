"""The self-hosted proof-of-work check and the escalating sign-in throttle."""
import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts import captcha, throttle
from apps.accounts.permissions import Role
from apps.core.models import SiteSettings

from .test_auth import STRONG, make_user

ON = dict(RAVIX_CAPTCHA_ENABLED=True, RAVIX_CAPTCHA_BITS=6)


def widget(html: str):
    """(token, bits) of the challenge drawn on a page, or None."""
    m = re.search(r'name="captcha_challenge" value="([^"]+)"', html)
    b = re.search(r'data-bits="(\d+)"', html)
    return (m.group(1), int(b.group(1))) if m and b else None


def answer(html: str) -> dict:
    token, bits = widget(html)
    return {"captcha_challenge": token, "captcha_solution": captcha.solve(token, bits)}


@override_settings(**ON)
class ProofOfWorkTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_correct_answer_works_once(self):
        c = captcha.new_challenge("register", 8)
        s = captcha.solve(c["token"], 8)
        self.assertEqual(captcha.verify("register", c["token"], s), "ok")
        self.assertEqual(captcha.verify("register", c["token"], s), "replay")

    def test_it_only_works_for_the_form_it_was_issued_for(self):
        c = captcha.new_challenge("register", 8)
        self.assertEqual(captcha.verify("reset", c["token"], captcha.solve(c["token"], 8)), "invalid")

    def test_wrong_missing_and_tampered_answers(self):
        c = captcha.new_challenge("register", 12)
        good = int(captcha.solve(c["token"], 12))
        bad = next(str(n) for n in range(good + 1, good + 500) if captcha.leading_zero_bits(__import__("hashlib").sha256(f"{c['token']}:{n}".encode()).digest()) < 12)
        self.assertEqual(captcha.verify("register", c["token"], bad), "invalid")
        self.assertEqual(captcha.verify("register", c["token"], ""), "missing")
        self.assertEqual(captcha.verify("register", "", "5"), "missing")
        self.assertEqual(captcha.verify("register", c["token"][:-3] + "abc", str(good)), "invalid")
        self.assertEqual(captcha.verify("register", c["token"], "-1"), "invalid")
        self.assertEqual(captcha.verify("register", c["token"], "٣"), "invalid")  # not an ASCII digit
        self.assertEqual(captcha.verify("register", c["token"], "9" * 40), "invalid")

    def test_an_easier_challenge_cannot_be_reused_after_the_bar_is_raised(self):
        c = captcha.new_challenge("login", 6)
        self.assertEqual(captcha.verify("login", c["token"], captcha.solve(c["token"], 6), min_bits=10), "weak")

    @override_settings(RAVIX_CAPTCHA_MAX_AGE=-1)
    def test_an_old_challenge_expires(self):
        c = captcha.new_challenge("register", 4)
        self.assertEqual(captcha.verify("register", c["token"], captcha.solve(c["token"], 4)), "expired")

    def test_difficulty_is_capped(self):
        self.assertEqual(captcha.new_challenge("x", 99)["bits"], captcha.MAX_BITS)

    def test_the_browser_hash_matches_the_server(self):
        # static/js/pow.js is checked against hashlib in the browser e2e; here, only that the file exists and exposes the solver
        from django.contrib.staticfiles import finders

        src = open(finders.find("js/pow.js"), encoding="utf-8").read()
        self.assertIn("RavixPow", src)
        self.assertNotIn("http", src)  # nothing is fetched from anywhere


@override_settings(**ON)
class PublicFormsAskForTheCheckTests(TestCase):
    def setUp(self):
        cache.clear()
        SiteSettings.objects.update_or_create(pk=1, defaults={"registration_open": True})
        self.reg = {"first_name": "N", "last_name": "R", "email": "pow@example.com", "password1": STRONG, "password2": STRONG}

    def test_sign_up_page_shows_the_check(self):
        html = self.client.get(reverse("accounts:register")).content.decode()
        self.assertIsNotNone(widget(html))
        self.assertIn("js/pow.js", html)

    def test_sign_up_without_the_check_creates_nobody_and_sends_nothing(self):
        r = self.client.post(reverse("accounts:register"), self.reg)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["form"].is_valid())
        self.assertEqual(r.context["form"].non_field_errors().as_data()[0].code, "captcha")
        self.assertFalse(__import__("django.contrib.auth", fromlist=["x"]).get_user_model().objects.filter(email="pow@example.com").exists())
        self.assertEqual(mail.outbox, [])

    def test_sign_up_with_the_check_works_and_an_answer_cannot_be_replayed(self):
        page = self.client.get(reverse("accounts:register")).content.decode()
        data = {**self.reg, **answer(page)}
        self.assertRedirects(self.client.post(reverse("accounts:register"), data), reverse("accounts:register_done"))
        self.assertEqual(len(mail.outbox), 1)
        again = self.client.post(reverse("accounts:register"), {**data, "email": "pow2@example.com"})
        self.assertEqual(again.status_code, 200)  # the same answer, spent
        self.assertEqual(len(mail.outbox), 1)

    def test_a_new_challenge_is_drawn_after_an_error(self):
        r = self.client.post(reverse("accounts:register"), self.reg)
        self.assertIsNotNone(widget(r.content.decode()))

    def test_password_recovery_needs_the_check(self):
        make_user("sara")
        url = reverse("accounts:password_reset")
        r = self.client.post(url, {"email": "sara@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(mail.outbox, [])
        page = self.client.get(url).content.decode()
        self.assertRedirects(self.client.post(url, {"email": "sara@example.com", **answer(page)}), reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_verification_needs_the_check(self):
        url = reverse("accounts:verify_resend")
        self.assertEqual(self.client.post(url, {"email": "x@example.com"}).status_code, 200)
        page = self.client.get(url).content.decode()
        self.assertEqual(self.client.post(url, {"email": "x@example.com", **answer(page)}).status_code, 302)

    @override_settings(RAVIX_CAPTCHA_ENABLED=False)
    def test_it_can_be_switched_off(self):
        html = self.client.get(reverse("accounts:register")).content.decode()
        self.assertIsNone(widget(html))
        r = self.client.post(reverse("accounts:register"), self.reg)
        self.assertRedirects(r, reverse("accounts:register_done"))

    @override_settings(RAVIX_CAPTCHA_ENABLED=False, RAVIX_REGISTER_LIMIT_PER_IP=3)
    def test_sign_up_attempts_per_address_are_limited(self):
        url = reverse("accounts:register")
        for i in range(3):
            self.assertEqual(self.client.post(url, {**self.reg, "email": f"a{i}@example.com"}, REMOTE_ADDR="198.51.100.9").status_code, 302)
        r = self.client.post(url, {**self.reg, "email": "a9@example.com"}, REMOTE_ADDR="198.51.100.9")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(__import__("django.contrib.auth", fromlist=["x"]).get_user_model().objects.filter(email="a9@example.com").exists())
        self.assertEqual(self.client.post(url, {**self.reg, "email": "b1@example.com"}, REMOTE_ADDR="198.51.100.10").status_code, 302)  # another address is unaffected


@override_settings(**ON)
class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("sara")
        self.url = reverse("accounts:login")

    def fail(self, n, ip="203.0.113.5", name="sara", extra=None):
        for _ in range(n):
            self.client.post(self.url, {"username": name, "password": "wrong-Pass-1", **(extra or {})}, REMOTE_ADDR=ip)

    def test_no_check_at_first_so_normal_sign_in_is_untouched(self):
        self.assertIsNone(widget(self.client.get(self.url).content.decode()))
        r = self.client.post(self.url, {"username": "sara", "password": STRONG})
        self.assertEqual(r.status_code, 302)

    def test_the_check_appears_after_a_few_failures_for_that_address_and_account(self):
        self.fail(2)
        self.assertIsNone(widget(self.client.get(self.url, REMOTE_ADDR="203.0.113.5").content.decode()))  # two failures: nothing yet
        self.fail(1)  # the third failure
        r = self.client.post(self.url, {"username": "sara", "password": "wrong-Pass-1"}, REMOTE_ADDR="203.0.113.5")
        self.assertIsNotNone(widget(r.content.decode()))

    def test_without_the_answer_not_even_the_right_password_works_and_no_password_is_tried(self):
        self.fail(3)
        before = throttle.failure_counts(self._req(), "sara")
        r = self.client.post(self.url, {"username": "sara", "password": STRONG}, REMOTE_ADDR="203.0.113.5")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(r.context["form"].non_field_errors().as_data()[0].code, "captcha")
        self.assertEqual(throttle.failure_counts(self._req(), "sara"), before)  # skipping the check does not even count as a guess

    def test_with_the_answer_the_right_password_signs_in(self):
        self.fail(3)
        page = self.client.post(self.url, {"username": "sara", "password": "x"}, REMOTE_ADDR="203.0.113.5").content.decode()
        r = self.client.post(self.url, {"username": "sara", "password": STRONG, **answer(page)}, REMOTE_ADDR="203.0.113.5")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_a_wrong_password_with_a_good_answer_is_still_a_failure(self):
        self.fail(3)
        page = self.client.post(self.url, {"username": "sara", "password": "x"}, REMOTE_ADDR="203.0.113.5").content.decode()
        before = throttle.failure_counts(self._req(), "sara")[0]
        self.client.post(self.url, {"username": "sara", "password": "nope-Pass-9", **answer(page)}, REMOTE_ADDR="203.0.113.5")
        self.assertEqual(throttle.failure_counts(self._req(), "sara")[0], before + 1)

    def test_the_puzzle_gets_harder_with_more_failures(self):
        self.fail(3)
        easy = widget(self.client.post(self.url, {"username": "sara", "password": "x"}, REMOTE_ADDR="203.0.113.5").content.decode())[1]
        self.fail(3, extra=None)  # the throttle will lock soon, so use a second address for the rest
        for ip in ("203.0.113.6", "203.0.113.7", "203.0.113.8"):
            self.fail(5, ip=ip)
        hard = widget(self.client.get(self.url, REMOTE_ADDR="203.0.113.99").content.decode())
        self.assertIsNone(hard)  # a fresh address has no failures of its own...
        page = self.client.post(self.url, {"username": "sara", "password": "x"}, REMOTE_ADDR="203.0.113.99").content.decode()
        self.assertGreater(widget(page)[1], easy)  # ...but the account has many, so it gets the harder puzzle

    def test_many_failures_against_one_account_from_many_addresses_raise_the_bar_for_everyone(self):
        for i in range(throttle.ACCOUNT_CAPTCHA_AFTER):
            self.fail(1, ip=f"198.51.100.{i + 1}")  # one failure each: no single address is suspicious
        r = self.client.post(self.url, {"username": "sara", "password": STRONG}, REMOTE_ADDR="192.0.2.77")
        self.assertEqual(r.status_code, 200)  # the owner is asked for the check too (they can solve it)...
        self.assertEqual(r.context["form"].non_field_errors().as_data()[0].code, "captcha")
        page = r.content.decode()
        r = self.client.post(self.url, {"username": "sara", "password": STRONG, **answer(page)}, REMOTE_ADDR="192.0.2.77")
        self.assertEqual(r.status_code, 302)  # ...and gets in: the account is never locked for good

    def test_a_distributed_attack_does_not_lock_the_owner_out(self):
        for i in range(30):
            self.fail(1, ip=f"198.51.100.{i + 1}")
        self.assertFalse(throttle.is_locked(self._req("192.0.2.77"), "sara"))

    def test_the_waiting_time_grows_and_never_becomes_permanent(self):
        waits = [throttle._wait_for(n, throttle.PAIR_TIERS) for n in (1, 4, 5, 8, 12, 20, 500)]
        self.assertEqual(waits, [0, 0, 60, 300, 900, 3600, 3600])
        self.assertTrue(all(w <= 3600 for w in waits))  # at most an hour at a time

    def test_five_failures_lock_that_address_and_account_for_a_while(self):
        for _ in range(throttle.MAX_ATTEMPTS):
            throttle.register_failure(self._req(), "sara")
        self.assertTrue(throttle.is_locked(self._req(), "sara"))
        self.assertFalse(throttle.is_locked(self._req("192.0.2.1"), "sara"))  # another address is not held back by it
        cache.delete(throttle._lock_key(throttle._key("203.0.113.5", "sara")))  # time passes
        self.assertFalse(throttle.is_locked(self._req(), "sara"))
        throttle.register_failure(self._req(), "sara")  # the next failure goes straight back into a (longer, once the count grows) wait
        self.assertTrue(throttle.is_locked(self._req(), "sara"))

    def test_one_address_trying_many_accounts_hits_the_address_wide_limit(self):
        for i in range(throttle.MAX_ATTEMPTS_PER_IP):
            throttle.register_failure(self._req(), f"user{i}")
        self.assertTrue(throttle.is_locked(self._req(), "anybody"))

    def test_a_good_sign_in_clears_the_counters_of_that_account(self):
        self.fail(2)
        self.client.post(self.url, {"username": "sara", "password": STRONG}, REMOTE_ADDR="203.0.113.5")
        self.assertEqual(throttle.failure_counts(self._req(), "sara")[0], 0)
        self.assertEqual(throttle.failure_counts(self._req(), "sara")[2], 0)

    def test_the_account_counter_does_not_keep_the_name_in_the_clear(self):
        throttle.register_failure(self._req(), "Sara@Example.com")
        self.assertNotIn("sara", throttle._account_key("Sara@Example.com"))
        self.assertEqual(throttle._account_key("SARA@example.com "), throttle._account_key("sara@example.com"))

    def _req(self, ip="203.0.113.5"):
        from django.test import RequestFactory

        return RequestFactory().get("/", REMOTE_ADDR=ip)
