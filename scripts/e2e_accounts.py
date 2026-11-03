#!/usr/bin/env python3
"""
End-to-end test of the public account pages in a real browser (Playwright + Chromium): sign-up with the self-hosted proof-of-work check
(the browser's SHA-256 must agree with the server's), the verification email, signing in, and the check that appears after failed sign-ins.

It creates a throw-away database, starts the dev server on a free port (the console email backend writes the emails to a log we read),
runs the scenarios and cleans up.  Run: python scripts/e2e_accounts.py     (HEADED=1 to watch, CHROMIUM_PATH for a custom browser)
"""
import os
import quopri
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("RAVIX_PYTHON") or sys.executable
PASSWORD = "Sup3r-Secret-Pass!"
results = []
ENV = {}


def stored_hash(email):
    """The password hash stored for the account (read straight from the database)."""
    code = "from django.contrib.auth import get_user_model as g; print('HASH=' + g().objects.get(email=%r).password)" % email
    out = subprocess.run([PY, "manage.py", "shell", "-v0", "-c", code], cwd=ROOT, env=ENV, capture_output=True, text=True, check=True).stdout
    return out.split("HASH=", 1)[1].strip()


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("  ✔ " if ok else "  ✘ ") + name + ("" if ok else f"   ← {detail}"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SEED = '''
from apps.core.models import SiteSettings
SiteSettings.objects.update_or_create(pk=1, defaults={"registration_open": True})
from django.contrib.auth import get_user_model
from apps.accounts.permissions import Role
get_user_model().objects.create_user("boss", "boss@example.com", "Sup3r-Secret-Pass!", role=Role.ADMIN)
from django.contrib.auth.hashers import make_password
legacy = get_user_model().objects.create_user("legacy", "legacy@example.com", "x", role=Role.ADMIN)
legacy.password = make_password("Legacy-Pass-2468x!", hasher="pbkdf2_sha256")  # an account made before Argon2
legacy.must_change_password = True
legacy.save()
print("seeded")
'''


def wait_for(fn, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.2)
    return None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravix-e2e-accounts-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    rel = os.path.relpath(tmp / "e2e.sqlite3", ROOT)
    log = tmp / "server.log"
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + rel, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONUNBUFFERED": "1",
           "RAVIX_CAPTCHA_ENABLED": "true", "RAVIX_CAPTCHA_BITS": "16", "RAVIX_INSTALLER_ENABLED": "false"}
    ENV.update(env)
    server = None
    try:
        subprocess.run([PY, "manage.py", "migrate", "-v0"], cwd=ROOT, env=env, check=True)
        subprocess.run([PY, "manage.py", "shell", "-v0"], cwd=ROOT, env=env, input=SEED.encode(), check=True)
        with open(log, "wb") as out:
            server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
            for _ in range(60):
                try:
                    socket.create_connection(("127.0.0.1", port), 0.5).close()
                    break
                except OSError:
                    time.sleep(0.5)
            run_scenarios(base, log)
    finally:
        if server:
            server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + ("" if not failed else "; failed: " + ", ".join(failed)))
    return 1 if failed else 0


def emailed_link(log, kind):
    """The last link of the given kind ("verify-email" or "invite") found in the console email log."""
    def find():
        text = quopri.decodestring(Path(log).read_bytes()).decode("utf-8", "replace")
        found = re.findall(r"https?://[^\s\"<>]*/accounts/" + kind + r"/[^\s\"<>]+", text)
        return found[-1] if found else None
    return wait_for(find)


def run_scenarios(base, log):
    with sync_playwright() as pw:
        kw = {"headless": not os.environ.get("HEADED")}
        if os.environ.get("CHROMIUM_PATH"):
            kw["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = pw.chromium.launch(**kw)
        problems = []

        print("\n[1] Sign-up: the browser solves the proof-of-work, the server accepts it")
        ctx = browser.new_context()
        page = ctx.new_page()
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.goto(base + "/accounts/register/")
        check("the check box is drawn", page.locator("[data-pow]").count() == 1)
        page.fill("#id_first_name", "Nima")
        page.fill("#id_last_name", "Rad")
        page.fill("#id_email", "nima@example.com")
        page.fill("#id_password1", PASSWORD)
        page.fill("#id_password2", PASSWORD)
        page.wait_for_selector("[data-pow][data-pow-state=done]", timeout=60000)
        check("the answer is in the form once the work is done", len(page.locator("[name=captcha_solution]").input_value()) > 0)
        check("the submit button is enabled only after that", page.locator("form button[type=submit]").is_enabled())
        page.click("form button[type=submit]")
        page.wait_for_url("**/accounts/register/done/")
        check("the server accepted the browser's answer and shows the generic page", "register/done" in page.url)

        print("\n[2] The verification email arrives and works")
        link = emailed_link(log, "verify-email")
        check("a verification link was emailed", link is not None)
        page.goto(link)
        page.click("form button[type=submit]")
        check("the address is verified", page.locator("a.btn[href*='/accounts/login/']").count() == 1)
        ctx.close()

        print("\n[3] Signing in; the check appears only after failures")
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(base + "/accounts/login/")
        check("no check on a normal sign-in page", page.locator("[data-pow]").count() == 0)
        for _ in range(3):
            page.fill("#id_username", "nima@example.com")
            page.fill("#id_password", "wrong-Pass-123")
            page.click("form button[type=submit]")
            page.wait_for_load_state()
        check("the check appears after three failures", page.locator("[data-pow]").count() == 1)
        page.fill("#id_username", "nima@example.com")
        page.fill("#id_password", PASSWORD)
        page.wait_for_selector("[data-pow][data-pow-state=done]", timeout=60000)
        page.click("form button[type=submit]")
        page.wait_for_url(lambda u: "/accounts/login" not in u)
        check("signed in as a member, on their own account page", "/accounts/profile" in page.url, page.url)
        ctx.close()

        print("\n[4] Without JavaScript the form cannot be sent in bulk")
        ctx = browser.new_context(java_script_enabled=False)
        page = ctx.new_page()
        page.goto(base + "/accounts/register/")
        page.fill("#id_first_name", "Bot")
        page.fill("#id_last_name", "X")
        page.fill("#id_email", "bot@example.com")
        page.fill("#id_password1", PASSWORD)
        page.fill("#id_password2", PASSWORD)
        page.click("form button[type=submit]")
        page.wait_for_load_state()
        check("it is refused and nobody is signed up", "/register/done" not in page.url and page.locator("[data-pow]").count() == 1)
        ctx.close()

        print("\n[5] An administrator invites someone, who accepts in the browser")
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(base + "/accounts/login/")
        page.fill("#id_username", "boss")
        page.fill("#id_password", PASSWORD)
        page.click("form button[type=submit]")
        page.wait_for_url(lambda u: "/accounts/login" not in u)
        page.goto(base + "/accounts/settings/")
        page.check("input[name=registration_mode][value=invite]")
        page.click("form:has(input[name=registration_mode]) button[type=submit]")
        page.wait_for_load_state()
        check("the sign-up mode was changed to invitation-only", page.locator("input[name=registration_mode][value=invite]").is_checked())
        page.goto(base + "/accounts/invitations/")
        page.fill("#id_email", "guest@example.com")
        page.select_option("#id_role", "author")
        page.click("form:has(#id_email) button[type=submit]")
        page.wait_for_load_state()
        invite = emailed_link(log, "invite")
        check("the invitation email carries a link", invite is not None)
        ctx.close()
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(base + "/accounts/register/")
        check("public sign-up is closed now", "/accounts/login" in page.url)
        page.goto(invite)
        check("the invitation shows the invited address", page.locator("text=guest@example.com").count() >= 1)
        page.fill("#id_first_name", "Guest")
        page.fill("#id_last_name", "User")
        page.fill("#id_password1", PASSWORD)
        page.fill("#id_password2", PASSWORD)
        page.click("form button[type=submit]")
        page.wait_for_url("**/accounts/login/**")
        page.fill("#id_username", "guest@example.com")
        page.fill("#id_password", PASSWORD)
        page.click("form button[type=submit]")
        page.wait_for_url(lambda u: "/accounts/login" not in u)
        check("the invited author signs in and reaches the panel (not the member page)", "/accounts/login" not in page.url and "/accounts/profile" not in page.url, page.url)
        page.goto(invite)
        check("the same link no longer works", page.locator("form input[name=password1]").count() == 0)
        ctx.close()

        print("\n[5] Argon2: an old PBKDF2 password is upgraded at sign-in; a forced change comes first")
        have_argon2 = subprocess.run([PY, "-c", "import argon2"], capture_output=True).returncode == 0
        check("the legacy account starts on PBKDF2", stored_hash("legacy@example.com").startswith("pbkdf2_sha256$"))
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(base + "/accounts/login/")
        page.fill("#id_username", "legacy@example.com")
        page.fill("#id_password", "Legacy-Pass-2468x!")
        page.click("form:has(#id_password) button[type=submit]")
        page.wait_for_url(lambda u: "/accounts/login" not in u)
        check("the flagged account lands on the password page", "/profile/password" in page.url, page.url)
        check("the page forces a change (no profile tabs)", page.locator("#id_new_password1").count() == 1)
        if have_argon2:
            check("signing in upgraded the stored hash to Argon2id", stored_hash("legacy@example.com").startswith("argon2$argon2id$"))
        page.goto(base + "/")
        check("any other page sends it back to the password page", "/profile/password" in page.url, page.url)
        page.fill("#id_old_password", "Legacy-Pass-2468x!")
        page.fill("#id_new_password1", "Legacy-Pass-2468x!")
        page.fill("#id_new_password2", "Legacy-Pass-2468x!")
        page.click("form:has(#id_new_password1) button[type=submit]")
        check("the old password is refused as the new one", "/profile/password" in page.url and page.locator(".error-text").count() >= 1, page.url)
        page.fill("#id_old_password", "Legacy-Pass-2468x!")
        page.fill("#id_new_password1", "summit-lantern-47-quiet")
        page.fill("#id_new_password2", "summit-lantern-47-quiet")
        page.click("form:has(#id_new_password1) button[type=submit]")
        page.goto(base + "/")
        check("after choosing a new password the site is open again", "/profile/password" not in page.url, page.url)
        if have_argon2:
            check("the new password is stored with Argon2id", stored_hash("legacy@example.com").startswith("argon2$argon2id$"))
        ctx.close()

        check("no unexpected console errors", not problems, problems[:3])
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
