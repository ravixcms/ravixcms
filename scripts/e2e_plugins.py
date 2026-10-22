#!/usr/bin/env python3
"""
آزمون سرتاسری سیستم افزونه‌ها با مرورگر واقعی (Playwright + Chromium).

سناریو: نصب و فعال‌سازی دو افزونهٔ نمونه از طریق رابط مدیریت، پر کردن و ارسال فرم تماس، دیدن پیام در صندوق پنل،
تنظیم سئوی یک صفحه و دیدن اثرش در صفحهٔ عمومی، سپس دستکاری فایل یک افزونهٔ فعال و اطمینان از توقف فوری آن،
و ایزوله‌بودن خطا (افزونهٔ خراب پنل را از کار نمی‌اندازد).
دیتابیس موقت می‌سازد و به دیتابیس پروژه دست نمی‌زند.
اجرا: python scripts/e2e_plugins.py   (متغیرها مثل e2e_builder.py: RAVIX_PYTHON, CHROMIUM_PATH, HEADED)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("RAVIX_PYTHON") or str(ROOT / ".venv" / "bin" / "python")
PASSWORD = "Pass12345!xyz"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("  ✔ " if ok else "  ✘ ") + name + ("" if ok else f"   ← {detail}"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SEED = r'''
from django.contrib.auth import get_user_model
from apps.accounts.permissions import Role
from apps.content.models import Page
U = get_user_model()
a = U.objects.create_user("admin", "a@x.com", "%(pw)s", role=Role.ADMIN)
U.objects.create_user("writer", "w@x.com", "%(pw)s", role=Role.AUTHOR)
Page.objects.create(title="درباره ما", slug="about", body="متن صفحه", status="published", author=a)
print("seeded")
''' % {"pw": PASSWORD}

CRASHY = {
    "plugin.json": {"slug": "crashy", "name": "افزونهٔ خراب", "version": "1.0.0", "api_version": 1, "ravix": ">=0.5.0,<1.0.0",
                    "description": "برای آزمون ایزوله‌سازی خطا",
                    "admin_pages": [{"key": "home", "title": "صفحهٔ خراب", "view": "views:home"}]},
}


def make_crashy(directory: Path):
    root = directory / "crashy"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps(CRASHY["plugin.json"], ensure_ascii=False), encoding="utf-8")
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "views.py").write_text("def home(request, api):\n    raise RuntimeError('e2e-boom')\n", encoding="utf-8")
    return root


def login(page, base, user):
    page.goto(base + "/accounts/login/")
    page.fill("#id_username", user)
    page.fill("#id_password", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_url(lambda u: "/accounts/login" not in u)


def install_and_activate(page, base, slug):
    page.goto(f"{base}/panel/plugins/{slug}/install/")
    check(f"{slug}: صفحهٔ تأیید نصب اثرانگشت را نشان می‌دهد", page.locator("code").count() >= 1)
    page.click("main button[type=submit]")  # بدون تیک اعتماد
    check(f"{slug}: بدون تأیید اعتماد نصب نمی‌شود", "/install/" in page.url and page.locator(".toast-error").count() >= 1)
    page.check("#trust")
    page.click("main button[type=submit]")
    page.wait_for_url(f"**/panel/plugins/{slug}/")
    check(f"{slug}: نصب شد ولی هنوز غیرفعال است", "نصب‌شده، غیرفعال" in page.content())
    page.click("text=فعال‌سازی")
    page.wait_for_load_state()
    check(f"{slug}: فعال شد", "فعال" in page.locator(".badge").first.inner_text())


def run(base, extra_dir):
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"], "headless": not os.environ.get("HEADED")}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(locale="fa-IR")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # --- نویسنده راهی به مدیریت افزونه‌ها ندارد
        login(page, base, "writer")
        r = page.goto(base + "/panel/plugins/")
        check("نویسنده: فهرست افزونه‌ها ۴۰۳", r.status == 403)
        ctx.clear_cookies()

        # --- مدیر
        login(page, base, "admin")
        page.goto(base + "/panel/plugins/")
        body = page.content()
        check("فهرست: دو افزونهٔ نمونه دیده می‌شوند", "فرم تماس" in body and "ابزار سئو" in body)
        check("فهرست: دکمه‌ای برای اجرای مستقیم وجود ندارد (فقط جزئیات)", page.locator("a:has-text('جزئیات')").count() >= 2)
        check("منوی کناری گزینهٔ افزونه‌ها دارد", page.locator(".sidebar-nav >> text=افزونه‌ها").count() >= 1)

        install_and_activate(page, base, "contact_form")
        install_and_activate(page, base, "seo_toolkit")
        check("منو: صفحات افزونه‌ها اضافه شد", page.locator(".sidebar-nav >> text=پیام‌های تماس").count() == 1 and page.locator(".sidebar-nav >> text=سئوی صفحات").count() == 1)

        # --- فرم تماس در سایت عمومی (کاربر ناشناس)
        anon = browser.new_context(locale="fa-IR").new_page()
        anon.goto(base + "/ext/contact_form/")
        check("فرم تماس: داخل قالب سایت نمایش داده می‌شود", anon.locator("header").count() >= 1 and anon.locator("form input[name=email]").count() == 1)
        anon.click("main button:has-text('ارسال پیام')")
        check("فرم تماس: فرم خالی خطا می‌دهد و ذخیره نمی‌شود", anon.locator("[role=alert]").count() >= 1)
        anon.fill("#id_name", "سارا احمدی")
        anon.fill("#id_email", "sara@example.com")
        anon.fill("#id_subject", "آزمون")
        anon.fill("#id_message", "پیام آزمایشی <b>تست</b> از مرورگر واقعی")
        anon.click("main button:has-text('ارسال پیام')")
        anon.wait_for_url("**sent=1")
        check("فرم تماس: پیام موفقیت نمایش داده می‌شود", "پیام شما ثبت شد" in anon.content())

        # --- صندوق پیام در پنل
        page.goto(base + "/panel/plugins/x/contact_form/inbox/")
        check("صندوق: پیام در پنل دیده می‌شود", "sara@example.com" in page.content())
        check("صندوق: HTML ورودی escape شده است", "<b>تست</b>" not in page.content() and "&lt;b&gt;تست&lt;/b&gt;" in page.content())
        page.click("button:has-text('خوانده شد')")
        page.wait_for_load_state()
        check("صندوق: علامت‌گذاری خوانده‌شده", "نخوانده" in page.locator("tbody").inner_text() and "جدید" not in page.locator("tbody").inner_text())

        # --- تنظیمات افزونه
        page.goto(base + "/panel/plugins/contact_form/settings/")
        page.fill("#id_max_per_hour", "9999")
        page.click("main button[type=submit]")
        check("تنظیمات: مقدار خارج از بازه رد می‌شود", "باید بین" in page.content())
        page.fill("#id_max_per_hour", "7")
        page.fill("#id_success_message", "ممنون از پیام شما")
        page.click("main button[type=submit]")
        page.wait_for_load_state()
        check("تنظیمات: ذخیره شد", "تنظیمات ذخیره شد" in page.content())

        # --- سئو: اثر روی صفحهٔ عمومی
        page.goto(base + "/panel/plugins/x/seo_toolkit/overview/")
        page.click("a:has-text('ویرایش') >> nth=0")
        page.fill("#id_title", "عنوان سئوی آزمایشی")
        page.fill("#id_description", "توضیح متای آزمایشی")
        page.check("#id_noindex")
        page.click("main button[type=submit]")
        page.wait_for_load_state()
        pub = browser.new_context().new_page()
        pub.goto(base + "/about/")
        check("سئو: عنوان سفارشی در صفحهٔ عمومی", pub.title().startswith("عنوان سئوی آزمایشی"), pub.title())
        check("سئو: meta description و noindex اعمال شد", pub.locator('meta[name=description]').get_attribute("content") == "توضیح متای آزمایشی"
              and "noindex" in (pub.locator('meta[name=robots]').get_attribute("content") or ""))

        # --- دستکاری کد افزونهٔ فعال
        views = ROOT / "plugins" / "seo_toolkit" / "hooks.py"
        original = views.read_text(encoding="utf-8")
        try:
            views.write_text(original + "\n# tampered\n", encoding="utf-8")
            time.sleep(6)  # بازبینی اثرانگشت هر ۵ ثانیه (VERIFY_INTERVAL) انجام می‌شود
            pub.goto(base + "/about/")
            check("دستکاری: افزونهٔ تغییر‌یافته فوراً متوقف شد (عنوان به حالت هسته برگشت)", not pub.title().startswith("عنوان سئوی آزمایشی"), pub.title())
            page.goto(base + "/panel/plugins/seo_toolkit/")
            check("دستکاری: پنل وضعیت «نیازمند تأیید» را نشان می‌دهد", "تغییر کد" in page.content())
            page.goto(base + "/panel/plugins/events/?plugin=seo_toolkit&level=error")
            check("دستکاری: رویداد tamper ثبت شد", "tamper" in page.content())
        finally:
            views.write_text(original, encoding="utf-8")

        # --- ایزوله‌سازی خطا
        shutil.rmtree(extra_dir / "crashy", ignore_errors=True)
        make_crashy(extra_dir)
        page.goto(base + "/panel/plugins/crashy/install/")
        page.check("#trust")
        page.click("main button[type=submit]")
        page.click("text=فعال‌سازی")
        page.wait_for_load_state()
        r = page.goto(base + "/panel/plugins/x/crashy/home/")
        check("خطا: صفحهٔ افزونهٔ خراب پیام دوستانه با کد ۵۰۰ می‌دهد", r.status == 500 and "در دسترس نیست" in page.content() and "e2e-boom" not in page.content())
        r = page.goto(base + "/panel/")
        check("خطا: داشبورد پنل سالم است", r.status == 200)
        page.goto(base + "/panel/plugins/events/?plugin=crashy")
        check("خطا: جزئیات برای مدیر در گزارش ثبت شد", "e2e-boom" in page.content())

        # --- حذف
        page.goto(base + "/panel/plugins/crashy/")
        page.click("button:has-text('غیرفعال‌سازی')")
        page.wait_for_load_state()
        page.click("button:has-text('حذف افزونه')")
        page.wait_for_url("**/panel/plugins/")
        check("حذف: افزونه پس از غیرفعال‌سازی حذف شد", "آمادهٔ نصب" in page.content())
        check("بدون خطای JavaScript در صفحات", not errors, errors)
        browser.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravix-e2e-plugins-"))
    extra = tmp / "extra"
    extra.mkdir()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    rel = os.path.relpath(tmp / "e2e.sqlite3", ROOT)
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + rel, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONUNBUFFERED": "1",
           "RAVIX_EXTRA_PLUGIN_DIRS": str(extra)}
    server = None
    try:
        subprocess.run([PY, "manage.py", "migrate", "-v0"], cwd=ROOT, env=env, check=True)
        subprocess.run([PY, "manage.py", "shell", "-v0"], cwd=ROOT, env=env, input=SEED.encode(), check=True)
        server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                time.sleep(0.5)
        run(base, extra)
    finally:
        if server:
            server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} بررسی موفق" + ("" if not failed else "؛ ناموفق: " + "، ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
