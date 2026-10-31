"""دامنهٔ اعتماد کلیدها: کلید افزونه‌ها (plugin) هسته را امضا نمی‌کند و کلید به‌روزرسانی (core) بستهٔ افزونه را؛ سازگاری با ردیف‌ها و env قدیمی."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils.translation import gettext, gettext_lazy
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.core import official
from apps.plugins import installer, signing, sources as psources
from apps.plugins.installer import InstallError
from apps.plugins.models import TrustedKey
from apps.updater import signing as usigning
from apps.updater import sources as usources
from apps.updater.errors import UpdateBlocked
from apps.updater.tests.helpers import UpdaterCase

from .test_installer import pkg, sig_for

PSEED, PPUB = signing.generate_keypair()  # «publisher» : فقط افزونه
CSEED, CPUB = signing.generate_keypair()  # «release» : فقط هسته
BSEED, BPUB = signing.generate_keypair()  # قدیمی بدون دامنه : هر دو
SHA = "a" * 64


def keys_env(*items):
    return override_settings(RAVIX_TRUSTED_PUBLIC_KEYS=list(items))


WRONG_SCOPE = gettext_lazy("This key is not trusted for this kind of package (plugin packages and core updates use separate trust scopes).")
NO_BUILTIN = mock.patch.object(official, "OFFICIAL_PUBLIC_KEYS", ())


class ParseTests(TestCase):
    def test_entry_formats(self):
        self.assertEqual(signing.parse_key_entry(PPUB, "env")[:3], ("env", PPUB, signing.ALL_SCOPES))
        self.assertEqual(signing.parse_key_entry(f"old:{PPUB}")[:3], ("old", PPUB, signing.ALL_SCOPES))
        self.assertFalse(signing.parse_key_entry(f"old:{PPUB}")[3])
        label, pub, scopes, explicit = signing.parse_key_entry(f"publisher:plugin:{PPUB}")
        self.assertEqual((label, pub, scopes, explicit), ("publisher", PPUB, frozenset({"plugin"}), True))
        self.assertEqual(signing.parse_key_entry(f"x:plugin+core:{PPUB}")[2], signing.ALL_SCOPES)
        self.assertEqual(signing.parse_key_entry(f"x:Core:{PPUB}")[2], frozenset({"core"}))

    def test_bad_entries(self):
        for bad in (f"x:admin:{PPUB}", f"x::{PPUB}", f"a:b:c:{PPUB}", "x:plugin:notakey", ""):
            with self.subTest(bad=bad), self.assertRaises(signing.SigningError):
                signing.parse_key_entry(bad)

    def test_trusted_keys_filter_by_scope(self):
        with NO_BUILTIN, keys_env(f"publisher:plugin:{PPUB}", f"release:core:{CPUB}", f"old:{BPUB}"):
            self.assertEqual({k.label for k in signing.trusted_keys().values()}, {"publisher", "release", "old"})
            self.assertEqual({k.label for k in signing.trusted_keys("plugin").values()}, {"publisher", "old"})
            self.assertEqual({k.label for k in signing.trusted_keys("core").values()}, {"release", "old"})


class PluginAndCoreSeparationTests(TestCase):
    def test_plugin_key_cannot_sign_core_and_core_key_cannot_sign_plugin(self):
        data = pkg()
        with NO_BUILTIN, keys_env(f"publisher:plugin:{PPUB}", f"release:core:{CPUB}"):
            ok_sig, ok_kid = sig_for(data, PSEED)
            self.assertTrue(signing.verify("demo_pkg", "1.0.0", *self._facts(data), ok_sig, ok_kid).ok)
            # کلید release بستهٔ افزونه را امضا می‌کند ولی پذیرفته نمی‌شود
            bad_sig, bad_kid = sig_for(data, CSEED)
            v = signing.verify("demo_pkg", "1.0.0", *self._facts(data), bad_sig, bad_kid)
            self.assertFalse(v.ok)
            self.assertEqual(v.reason, str(WRONG_SCOPE))
            with self.assertRaises(InstallError):
                installer.install_from_file(data, make_user("boss", Role.SUPER_ADMIN), signature=bad_sig, key_id=bad_kid)
            # بدون ادعای key_id هم نباید با کلید دیگری جور شود
            self.assertFalse(signing.verify("demo_pkg", "1.0.0", *self._facts(data), bad_sig, "").ok)
            # کلید ناشر افزونه، بستهٔ هسته و فهرست را امضا می‌کند ولی پذیرفته نمی‌شود
            sig, kid = usigning.sign(PSEED, "core", "", "0.7.0", SHA)
            self.assertFalse(usigning.verify("core", "", "0.7.0", SHA, sig, kid).ok)
            self.assertFalse(usigning.verify("core", "", "0.7.0", SHA, sig, "").ok)
            isig, ikid = usigning.sign_index(PSEED, b"{}")
            self.assertFalse(usigning.verify_index(b"{}", isig, ikid).ok)
            # و کلید release درست
            sig, kid = usigning.sign(CSEED, "core", "", "0.7.0", SHA)
            self.assertTrue(usigning.verify("core", "", "0.7.0", SHA, sig, kid).ok)
            isig, ikid = usigning.sign_index(CSEED, b"{}")
            self.assertTrue(usigning.verify_index(b"{}", isig, ikid).ok)

    @staticmethod
    def _facts(data):
        from apps.plugins.package import inspect

        i = inspect(data)
        return i.sha256, i.fingerprint

    def test_wrong_scope_reason_is_explicit(self):
        with NO_BUILTIN, keys_env(f"publisher:plugin:{PPUB}"):
            sig, kid = usigning.sign(PSEED, "core", "", "0.7.0", SHA)
            v = usigning.verify("core", "", "0.7.0", SHA, sig, kid)
            self.assertEqual(v.state, "invalid")
            self.assertEqual(v.reason, str(WRONG_SCOPE))
            self.assertNotEqual(v.reason, gettext("The signing key is not in the list of trusted keys (or it has been disabled)."))

    def test_legacy_env_entry_keeps_working_for_both(self):
        with NO_BUILTIN, keys_env(f"old:{BPUB}"):
            sig, kid = usigning.sign(BSEED, "core", "", "0.7.0", SHA)
            self.assertTrue(usigning.verify("core", "", "0.7.0", SHA, sig, kid).ok)
            data = pkg()
            psig, pkid = sig_for(data, BSEED)
            self.assertTrue(signing.verify("demo_pkg", "1.0.0", *self._facts(data), psig, pkid).ok)

    def test_revocation_still_wins(self):
        with NO_BUILTIN, keys_env(f"release:core:{CPUB}"), override_settings(RAVIX_REVOKED_KEY_IDS=[signing.key_id(CPUB)]):
            sig, kid = usigning.sign(CSEED, "core", "", "0.7.0", SHA)
            self.assertFalse(usigning.verify("core", "", "0.7.0", SHA, sig, kid).ok)

    def test_source_states_follow_the_scope(self):
        urls = dict(RAVIX_UPDATE_URL="https://updates.example.com", RAVIX_UPDATE_DIR="", RAVIX_OFFICIAL_REGISTRY_URL="https://plugins.example.com")
        with NO_BUILTIN, override_settings(**urls), keys_env(f"publisher:plugin:{PPUB}"):
            self.assertEqual(usources.source_state(), "no_key")
            self.assertEqual(psources.registry_state(), "ok")
        with NO_BUILTIN, override_settings(**urls), keys_env(f"release:core:{CPUB}"):
            self.assertEqual(usources.source_state(), "ok")
            self.assertEqual(psources.registry_state(), "no_key")


class DatabaseKeyTests(TestCase):
    def test_existing_rows_keep_both_scopes(self):
        k = TrustedKey.objects.create(key_id=signing.key_id(BPUB), public_key=BPUB, label="old")
        self.assertEqual(k.scopes, "plugin,core")
        self.assertEqual(k.scope_set, signing.ALL_SCOPES)
        with NO_BUILTIN:
            self.assertIn(k.key_id, signing.trusted_keys("plugin"))
            self.assertIn(k.key_id, signing.trusted_keys("core"))

    def test_corrupt_scope_value_trusts_nothing(self):
        k = TrustedKey.objects.create(key_id=signing.key_id(BPUB), public_key=BPUB, label="x", scopes="root")
        self.assertEqual(k.scope_set, frozenset())
        with NO_BUILTIN:
            self.assertNotIn(k.key_id, signing.trusted_keys("plugin"))
            self.assertNotIn(k.key_id, signing.trusted_keys("core"))

    def test_panel_add_defaults_to_plugin_only_and_can_be_narrowed(self):
        c = Client()
        c.force_login(make_user("boss", Role.SUPER_ADMIN))
        url = reverse("plugins:trusted_keys")
        self.assertEqual(c.post(url, {"op": "add", "label": "p", "public_key": PPUB}).status_code, 302)
        k = TrustedKey.objects.get()
        self.assertEqual(k.scopes, "plugin")
        self.assertEqual(c.post(url, {"op": "scopes", "pk": k.pk, "scopes": ["plugin", "core"]}).status_code, 302)
        k.refresh_from_db()
        self.assertEqual(k.scopes, "plugin,core")
        self.assertEqual(c.post(url, {"op": "scopes", "pk": k.pk}).status_code, 400)  # حداقل یک دامنه
        self.assertEqual(c.post(url, {"op": "scopes", "pk": k.pk, "scopes": ["root"]}).status_code, 400)
        self.assertEqual(c.post(url, {"op": "add", "label": "q", "public_key": CPUB, "scopes": ["bogus"]}).status_code, 400)
        self.assertEqual(c.post(url, {"op": "add", "label": "q", "public_key": CPUB, "scopes": ["core"]}).status_code, 302)
        self.assertEqual(TrustedKey.objects.get(key_id=signing.key_id(CPUB)).scopes, "core")
        self.assertEqual(c.get(url).status_code, 200)

    def test_scopes_change_needs_the_trust_capability(self):
        k = TrustedKey.objects.create(key_id=signing.key_id(BPUB), public_key=BPUB, label="old")
        c = Client()
        c.force_login(make_user("ed", Role.ADMIN))
        self.assertEqual(c.post(reverse("plugins:trusted_keys"), {"op": "scopes", "pk": k.pk, "scopes": ["core"]}).status_code, 403)
        k.refresh_from_db()
        self.assertEqual(k.scopes, "plugin,core")


class PreflightTests(TestCase):
    def run_preflight(self):
        out = StringIO()
        try:
            call_command("ravix_preflight", "--ci", stdout=out, stderr=StringIO())
        except SystemExit:
            pass
        return out.getvalue()

    def test_legacy_and_bad_entries_are_reported(self):
        with keys_env(f"old:{PPUB}"):
            self.assertIn("RAVIX_TRUSTED_PUBLIC_KEYS", self.run_preflight())
            self.assertIn(f"old:{PPUB}"[:20], self.run_preflight())
        with keys_env(f"x:admin:{CPUB}"):
            self.assertIn(f"x:admin:{CPUB}"[:20], self.run_preflight())

    def test_scoped_entries_raise_no_warning(self):
        with NO_BUILTIN, keys_env(f"publisher:plugin:{PPUB}"):
            text = self.run_preflight()
        self.assertNotIn(f"publisher:plugin:{PPUB}"[:20], text)


class PipelineScopeTests(UpdaterCase):
    def test_core_update_signed_by_a_plugin_only_key_is_blocked(self):
        from apps.updater.tests.helpers import SEED, PUB

        self.publish("0.7.0")
        with NO_BUILTIN, keys_env(f"publisher:plugin:{PUB}"):
            with self.assertRaises(UpdateBlocked):
                self.apply()
            self.assertEqual(self.disk(), "0.6.1")
        with NO_BUILTIN, keys_env(f"release:core:{PUB}"):
            self.assertEqual(self.apply().status, "succeeded")
