"""Release integrity: manifest of every file, hashed dependency locks, per-language notes, channels, the offline signer and the private-key guard."""
import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from apps.plugins import signing as plugin_signing
from apps.updater import deps, manifest, package, release, signing, sources
from apps.updater.errors import UpdateError

from .helpers import PUB, SEED, UpdaterCase, make_tree

ROOT = Path(settings.BASE_DIR)
HASH = "ab" * 32


class DepsTests(SimpleTestCase):
    LOCK = "Django==5.2.18\njalali_core==1.0.0\n# comment\n"

    def hashed(self, *names):
        return "".join(f"{n} \\\n    --hash=sha256:{HASH} \\\n    --hash=sha256:{'cd' * 32}\n" for n in names)

    def test_names_are_compared_the_way_pip_does(self):
        self.assertEqual(deps.norm("Jalali_Core"), "jalali-core")
        self.assertEqual(deps.parse_lock(self.LOCK), {"django": "5.2.18", "jalali-core": "1.0.0"})

    def test_consistent_locks_have_no_problems(self):
        self.assertEqual(deps.problems(self.LOCK, self.hashed("Django==5.2.18", "jalali-core==1.0.0")), [])

    def test_missing_wrong_extra_and_hashless_entries_are_reported(self):
        self.assertEqual(len(deps.problems(self.LOCK, self.hashed("Django==5.2.18"))), 1)  # jalali-core is missing
        self.assertEqual(len(deps.problems(self.LOCK, self.hashed("Django==5.2.17", "jalali-core==1.0.0"))), 1)  # another version
        self.assertEqual(len(deps.problems(self.LOCK, self.hashed("Django==5.2.18", "jalali-core==1.0.0", "evil==1.0"))), 1)  # a package the lock does not have
        self.assertEqual(len(deps.problems(self.LOCK, "Django==5.2.18\njalali-core==1.0.0\n")), 2)  # no hashes at all
        self.assertTrue(deps.problems("Django>=5\n", ""))  # not an exact pin

    def test_changes_lists_only_what_differs_from_the_installed_packages(self):
        self.assertEqual(deps.changes(self.LOCK, {"django": "5.2.18", "jalali-core": "1.0.0"}), [])
        self.assertEqual(deps.changes(self.LOCK, {"django": "5.2.1"}), [("django", "5.2.1", "5.2.18"), ("jalali-core", "", "1.0.0")])

    def test_this_repository_has_a_consistent_hashed_lock(self):
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        hashes = (ROOT / "requirements.hashes.lock").read_text(encoding="utf-8")
        self.assertEqual(deps.problems(lock, hashes), [])
        for name, (_version, found) in deps.parse_hash_lock(hashes).items():
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", h) for h in found), name)

    def test_the_hash_script_pins_the_same_packages(self):
        spec = importlib.util.spec_from_file_location("make_hash_lock", ROOT / "scripts" / "make_hash_lock.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        with mock.patch.object(mod, "fetch_hashes", side_effect=lambda n, v: [HASH]):
            out = mod.render([(n, v, mod.fetch_hashes(n, v)) for n, v in mod.pins(text)])
        self.assertEqual(deps.problems(text, out), [])


class ManifestTests(UpdaterCase):
    def build(self, version="0.9.9", **kw):
        tree = make_tree(self.src / version, version, **kw)
        release.build_core(tree, self.dist, seed=None, requires={"plugin_api": 1, "theme_api": 1})
        out = self.base / "x"
        shutil.rmtree(out, ignore_errors=True)
        root = package.extract((self.dist / f"ravix-core-{version}.zip").read_bytes(), out)
        return tree, root

    def test_a_built_package_carries_a_manifest_that_matches_every_file(self):
        tree, root = self.build()
        listed = manifest.read(root)
        self.assertIn("manage.py", listed)
        self.assertIn("requirements.hashes.lock", listed)
        self.assertNotIn("manifest.json", listed)
        self.assertEqual(listed, manifest.scan(tree, [e for e in package.CORE_ENTRIES if (tree / e).exists() and e != "manifest.json"]))
        rel = package.load_release(root)
        self.assertTrue(rel["manifest"] and rel["deps_hashes"])
        self.assertEqual(package.validate_core(root, "0.9.9").files, len(listed) + 2)  # + manifest.json and release.json

    def test_local_report_names_the_owners_files_and_check_prints_them(self):
        tree, _root = self.build()
        (tree / "manifest.json").write_bytes(manifest.dumps(manifest.scan(tree, [e for e in package.CORE_ENTRIES if (tree / e).exists() and e != "manifest.json"])))
        self.assertEqual(manifest.local_report(tree, package.CORE_ENTRIES), {"modified": [], "added": []})
        (tree / "static" / "app.css").write_text("/* mine */")
        (tree / "static" / "mine.css").write_text("/* extra */")
        rep = manifest.local_report(tree, package.CORE_ENTRIES)
        self.assertEqual(rep["modified"], ["static/app.css"])
        self.assertEqual(rep["added"], ["static/mine.css"])
        from apps.updater.management.commands.ravix_update import Command
        out = StringIO()
        cmd = Command(stdout=out)
        with mock.patch("apps.updater.versions.app_root", return_value=tree):
            cmd._manual_files()
        self.assertIn("static/app.css", out.getvalue())
        self.assertIn("static/mine.css", out.getvalue())
        (tree / "manifest.json").unlink()
        self.assertIsNone(manifest.local_report(tree, package.CORE_ENTRIES))

    def test_a_changed_extra_or_missing_file_is_refused(self):
        for action in ("change", "add", "remove"):
            _tree, root = self.build()
            if action == "change":
                (root / "static" / "app.css").write_text("/* swapped */")
            elif action == "add":
                (root / "static" / "extra.css").write_text("x")
            else:
                (root / "docs" / "readme.md").unlink()
            with self.assertRaises(UpdateError, msg=action):
                package.validate_core(root, "0.9.9")

    def test_a_package_without_hash_locks_cannot_be_built_or_is_refused(self):
        tree = make_tree(self.src / "0.9.8", "0.9.8")
        (tree / "requirements.hashes.lock").unlink()
        with self.assertRaises(UpdateError):
            release.build_core(tree, self.dist, seed=None)
        tree2 = make_tree(self.src / "0.9.7", "0.9.7", extra={"requirements.hashes.lock": "asgiref==3.12.0 \\\n    --hash=sha256:" + HASH + "\n"})
        with self.assertRaises(UpdateError):
            release.build_core(tree2, self.dist, seed=None)

    def test_compare_tells_the_owners_files_from_the_releases(self):
        tree, root = self.build()
        new = manifest.read(root)
        site = self.root
        entries = [e for e in package.CORE_ENTRIES if e != "manifest.json"]
        installed = manifest.scan(site, entries)
        (site / "manifest.json").write_bytes(manifest.dumps(installed))
        (site / "templates" / "base.html").write_text("<p>mine</p>")           # edited
        (site / "templates" / "mine.html").write_text("<p>new file</p>")       # added
        (site / "templates" / "__pycache__").mkdir(exist_ok=True)
        (site / "templates" / "__pycache__" / "x.pyc").write_bytes(b"x")       # never counted
        result = manifest.compare(site, entries, new, manifest.read(site))
        self.assertEqual(result["modified"], ["templates/base.html"])
        self.assertEqual(result["added"], ["templates/mine.html"])
        self.assertTrue(result["known_baseline"])
        # an install without an installed manifest (before 0.10.0): everything the new release does not have is listed as extra
        (site / "manifest.json").unlink()
        blind = manifest.compare(site, entries, new, None)
        self.assertIn("templates/mine.html", blind["added"])
        self.assertFalse(blind["known_baseline"])

    def test_the_installed_manifest_is_kept_after_an_update(self):
        self.publish("0.9.9")
        self.apply("0.9.9")
        self.assertIn("manage.py", manifest.read(self.root))


class NotesAndChannelTests(UpdaterCase):
    def test_notes_per_language_are_cleaned_and_served(self):
        entry = self.publish("0.9.9")
        self.assertNotIn("notes_i18n", entry)
        tree = make_tree(self.src / "0.9.10", "0.9.10")
        entry = release.build_core(tree, self.dist, seed=SEED, requires={}, notes="plain", notes_i18n={"fa": " فارسی ", "en": "English", "de": ""})
        self.assertEqual(entry["notes_i18n"], {"fa": "فارسی", "en": "English"})
        clean = sources.normalize_entry(entry, "core")
        self.assertEqual(sources.notes_for(clean, "fa"), "فارسی")
        self.assertEqual(sources.notes_for(clean, "pt-br"), "English")  # no pt_BR text: English, then the plain notes
        self.assertEqual(sources.notes_for({"notes": "plain", "notes_i18n": {}}, "fa"), "plain")
        self.assertEqual(sources.notes_for({"notes": "plain", "notes_i18n": {"zh_Hans": "中文"}}, "zh-hans"), "中文")

    def test_unknown_languages_and_non_text_notes_are_refused_when_publishing(self):
        for bad in ({"xx": "hi"}, {"fa": 5}, ["fa"]):
            with self.assertRaises(UpdateError):
                release.clean_notes_i18n(bad)

    def test_an_index_cannot_smuggle_markup_sized_notes(self):
        raw = {"version": "0.9.9", "sha256": "a" * 64, "size": 10, "requires": {}, "notes_i18n": {"fa": "x" * 5000, "zz": "no", "en": 5}}
        entry = sources.normalize_entry(raw, "core")
        self.assertEqual(list(entry["notes_i18n"]), ["fa"])
        self.assertEqual(len(entry["notes_i18n"]["fa"]), 2000)

    def test_the_channel_of_the_index_must_match_the_site(self):
        data = json.dumps({"schema": 1, "channel": "beta", "core": []}).encode()
        with self.assertRaises(UpdateError):
            sources.parse_index(data)
        with override_settings(RAVIX_UPDATE_CHANNEL="beta"):
            self.assertEqual(sources.parse_index(data)["channel"], "beta")
            with self.assertRaises(UpdateError):
                sources.parse_index(json.dumps({"schema": 1, "core": []}).encode())  # no channel means stable
        self.assertEqual(sources.parse_index(json.dumps({"schema": 1, "core": []}).encode())["channel"], "")

    def test_a_beta_site_reads_the_beta_folder(self):
        seen = []
        with override_settings(RAVIX_UPDATE_CHANNEL="beta"), mock.patch.object(sources, "_http_get", side_effect=lambda url, *a, **k: (seen.append(url) or (b'{"schema":1,"channel":"beta","core":[]}', {}))):
            sources.HttpSource("https://u.example.com").index()
        self.assertEqual(seen[0], "https://u.example.com/updates/v1/beta/index.json")
        seen.clear()
        with mock.patch.object(sources, "_http_get", side_effect=lambda url, *a, **k: (seen.append(url) or (b'{"schema":1,"core":[]}', {}))):
            sources.HttpSource("https://u.example.com").index()
        self.assertEqual(seen[0], "https://u.example.com/updates/v1/index.json")

    def test_a_weird_channel_name_falls_back_to_stable(self):
        with override_settings(RAVIX_UPDATE_CHANNEL="../evil"):
            self.assertEqual(sources.channel(), "stable")

    def test_the_command_writes_notes_and_channel(self):
        notes = self.base / "notes.json"
        notes.write_text(json.dumps({"fa": "نسخهٔ تازه", "en": "New"}), encoding="utf-8")
        tree = make_tree(self.src / "0.9.11", "0.9.11")
        out = StringIO()
        call_command("ravix_release", "core", "--source", str(tree), "--out", str(self.dist), "--no-sign", "--notes-i18n", str(notes), "--channel", "beta", stdout=out)
        doc = json.loads((self.dist / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["channel"], "beta")
        self.assertEqual(doc["core"][0]["notes_i18n"]["fa"], "نسخهٔ تازه")
        self.assertNotIn("signature", doc["core"][0])
        self.assertFalse((self.dist / "index.json.sig").exists())


class OfflineSignerTests(UpdaterCase):
    SCRIPT = ROOT / "scripts" / "ravix_sign.py"

    def unsigned_folder(self, version="0.9.9"):
        tree = make_tree(self.src / version, version)
        call_command("ravix_release", "core", "--source", str(tree), "--out", str(self.dist), "--no-sign", stdout=StringIO())
        return self.dist

    def key_file(self, seed=SEED):
        path = self.base / "release.key"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(seed)
        return path

    def run_script(self, *args):
        return subprocess.run([sys.executable, "-I", str(self.SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=120)

    def test_the_standalone_script_uses_the_same_protocol_as_the_site(self):
        spec = importlib.util.spec_from_file_location("ravix_sign", self.SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.message("core", "", "0.9.9", "a" * 64), signing.message("core", "", "0.9.9", "a" * 64))
        self.assertEqual(mod.message("theme", "x", "1.0.0", "b" * 64), signing.message("theme", "x", "1.0.0", "b" * 64))
        self.assertEqual(mod.index_message("c" * 64), signing.index_message("c" * 64))
        self.assertEqual(mod.key_id(base64.b64decode(PUB)), plugin_signing.key_id(PUB))

    def test_a_folder_built_without_a_signature_is_signed_offline_and_accepted_by_the_site(self):
        folder = self.unsigned_folder()
        proc = self.run_script(folder, "--key-file", self.key_file())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("signed 1 package", proc.stdout)
        self.assertNotIn(SEED, proc.stdout + proc.stderr)  # the private key is never printed
        with override_settings(RAVIX_UPDATE_REQUIRE_SIGNED_INDEX=True):
            index = sources.DirSource(folder).index()  # index.json.sig verifies
        entry = index["core"][0]
        self.assertTrue(signing.verify("core", "", "0.9.9", entry["sha256"], entry["signature"], entry["signature_key_id"]).ok)
        check = self.run_script(folder, "--check", PUB)
        self.assertEqual(check.returncode, 0, check.stdout)

    def test_an_offline_signed_release_installs(self):
        folder = self.unsigned_folder()
        self.assertEqual(self.run_script(folder, "--key-file", self.key_file()).returncode, 0)
        self.assertEqual(self.apply("0.9.9").status, "succeeded")

    def test_a_package_changed_after_the_index_was_built_is_not_signed(self):
        folder = self.unsigned_folder()
        with zipfile.ZipFile(folder / "ravix-core-0.9.9.zip", "a") as zf:
            zf.writestr("ravix-0.9.9/extra.txt", "x")
        proc = self.run_script(folder, "--key-file", self.key_file())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Nothing was signed", proc.stderr)
        self.assertNotIn("signature", json.loads((folder / "index.json").read_text())["core"][0])

    def test_the_wrong_key_and_loose_permissions_are_caught(self):
        folder = self.unsigned_folder()
        other, other_pub = plugin_signing.generate_keypair()
        self.assertEqual(self.run_script(folder, "--key-file", self.key_file(other)).returncode, 0)
        self.assertNotEqual(self.run_script(folder, "--check", PUB).returncode, 0)  # signed by another key
        if os.name == "posix":
            key = self.key_file()
            os.chmod(key, 0o644)
            self.assertNotEqual(self.run_script(folder, "--key-file", key, "--resign").returncode, 0)

    def test_it_needs_only_the_standard_library_and_cryptography(self):
        tree = __import__("ast").parse(self.SCRIPT.read_text(encoding="utf-8"))
        names = set()
        for node in __import__("ast").walk(tree):
            if isinstance(node, __import__("ast").Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, __import__("ast").ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertTrue(names <= set(sys.stdlib_module_names) | {"cryptography"}, names)


class PrivateKeyNeverOnTheSiteTests(TestCase):
    """The release signing key is read by the publisher's tools only; the web process, the update executor and the release plugin never read it."""

    PUBLISHER_TOOLS = {"apps/updater/management/commands/ravix_release.py", "apps/updater/management/commands/ravin_release.py"}
    WARNS_ABOUT_IT = {"apps/core/management/commands/ravix_preflight.py"}  # only to tell the owner to remove it from the site's environment

    def test_only_the_publisher_commands_read_the_release_key_variable(self):
        found = set()
        for base in ("apps", "config"):
            for path in sorted((ROOT / base).rglob("*.py")):
                rel = path.relative_to(ROOT).as_posix()
                if "/tests/" not in rel and "RELEASE_SIGNING_KEY" in path.read_text(encoding="utf-8"):
                    found.add(rel)
        self.assertTrue(found <= self.PUBLISHER_TOOLS | self.WARNS_ABOUT_IT, found)
        self.assertIn("apps/updater/management/commands/ravix_release.py", found)

    def test_the_update_runtime_has_no_signing_code(self):
        updater = ROOT / "apps" / "updater"
        runtime = [p for p in sorted(updater.glob("*.py")) if p.name not in ("release.py", "signing.py")]
        self.assertTrue(len(runtime) > 8)
        for path in runtime:
            text = path.read_text(encoding="utf-8")
            for token in ("signing.sign(", "sign_index(", "private_bytes", "read_key_file", "load_private_key", "RELEASE_SIGNING_KEY"):
                self.assertNotIn(token, text, f"{path.name}: {token}")
        for path in sorted((updater / "management" / "commands").glob("*.py")):
            if path.name not in ("ravix_release.py", "ravin_release.py"):
                self.assertNotIn("read_key_file", path.read_text(encoding="utf-8"), path.name)

    def test_the_preflight_warns_when_the_key_is_in_the_environment(self):
        from django.core.management import call_command as cc

        out = StringIO()
        with mock.patch.dict(os.environ, {"RAVIX_RELEASE_SIGNING_KEY": "x"}):
            try:
                cc("ravix_preflight", stdout=out, stderr=out)
            except SystemExit:
                pass
        self.assertIn("RAVIX_RELEASE_SIGNING_KEY", out.getvalue())
