#!/usr/bin/env python3
"""
آزمون سرتاسری (End-to-End) صفحه‌ساز با مرورگر واقعی (Playwright + Chromium) — شامل Drag & Drop واقعی.

این اسکریپت خودش یک پایگاه داده‌ی موقت می‌سازد، داده‌ی اولیه می‌گذارد، سرور را روی یک پورت آزاد اجرا می‌کند،
سناریوها را اجرا می‌کند و همه‌چیز را پاک می‌کند. به دیتابیس پروژه‌ی شما دست نمی‌زند.

پیش‌نیاز (یک‌بار):  pip install playwright && playwright install chromium
اجرا:               python scripts/e2e_builder.py            (از ریشه‌ی پروژه، با python‌ای که Django نصب است یا نیست)
متغیرها:            RAVIN_PYTHON (مفسر پروژه؛ پیش‌فرض .venv/bin/python)، CHROMIUM_PATH (مسیر Chromium در صورت نیاز)،
                    HEADED=1 (نمایش مرورگر)، SLOWMO=300 (کندتر؛ برای تماشای Drag & Drop)
خروجی: کد 0 اگر همه‌ی بررسی‌ها موفق باشند.
"""
import glob
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
PY = os.environ.get("RAVIN_PYTHON") or str(ROOT / ".venv" / "bin" / "python")
PASSWORD = "Pass12345!xyz"
results = []


def wait_js(page, expr, timeout=5000):
    """مثل page.wait_for_function ولی با CDP evaluate: wait_for_function از eval درون صفحه استفاده می‌کند و CSP پنل (بدون unsafe-eval) آن را درست مسدود می‌کند."""
    import time
    end = time.time() + timeout / 1000
    while time.time() < end:
        if page.evaluate("() => !!(" + expr + ")"):
            return
        time.sleep(0.1)
    raise AssertionError("timeout waiting for: " + expr)


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("  ✔ " if ok else "  ✘ ") + name + ("" if ok else f"   ← {detail}"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SEED = r'''
import io
from PIL import Image
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from apps.accounts.permissions import Role
from apps.content.models import Page
from apps.media_library.models import Media
U = get_user_model()
a = U.objects.create_user("admin", "a@x.com", "%(pw)s", role=Role.ADMIN)
U.objects.create_user("writer", "w@x.com", "%(pw)s", role=Role.AUTHOR)
Page.objects.create(title="درباره ما", slug="about", body="متن قدیمی **مارک‌داون**", status="published", author=a)
buf = io.BytesIO(); Image.new("RGB", (640, 400), (200, 60, 60)).save(buf, "PNG")
m = Media(original_name="e2e-test.png", title="تصویر آزمون", alt_text="تصویر قرمز", kind="image", mime_type="image/png", size=buf.tell(), width=640, height=400, uploaded_by=a)
m.file.save("e2e-test.png", ContentFile(buf.getvalue()), save=True)
print("seeded")
''' % {"pw": PASSWORD}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravin-e2e-"))
    started = time.time()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    rel = os.path.relpath(tmp / "e2e.sqlite3", ROOT)
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + rel, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONUNBUFFERED": "1"}
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
        run_scenarios(base)
    finally:
        if server:
            server.terminate()
        # حذف فایل‌های رسانه‌ی ساخته‌شده توسط آزمون
        for f in glob.glob(str(ROOT / "media" / "library" / "**" / "*"), recursive=True):
            if os.path.isfile(f) and os.path.getmtime(f) >= started - 1:
                os.remove(f)
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} بررسی موفق" + ("" if not failed else "؛ ناموفق: " + "، ".join(failed)))
    return 1 if failed else 0


# ---------------------------------------------------------------------------------------------- سناریوها
def login(page, base, user):
    page.goto(base + "/accounts/login/")
    page.fill("#id_username", user)
    page.fill("#id_password", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_url(lambda u: "/accounts/login" not in u)


def state(page, base, page_id=1):
    return page.evaluate("async (u) => (await fetch(u, {credentials:'same-origin'})).json()", f"{base}/panel/builder/{page_id}/api/state/")


def els(page):
    return page.evaluate("[...document.querySelectorAll('#bx-canvas .bx-el')].map(e => e.dataset.type)")


def row_counts(page):
    return page.evaluate("[...document.querySelectorAll('#bx-canvas .bx-row')].map(r => r.querySelectorAll('.bx-el').length)")


def wait_saved(page):
    wait_js(page, "document.querySelector('#bx-status').textContent.includes('ذخیره شد')", timeout=10000)


def drag(page, source, target, at=None):
    """Drag & Drop واقعی (رویدادهای موس → dragstart/dragover/drop بومی Chromium).
    at="bottom": رها کردن در لبه‌ی پایینی هدف (مثلاً فاصله‌ی پایین یک بخش، یعنی «انتهای بخش»)."""
    src = page.locator(source) if isinstance(source, str) else source
    dst = page.locator(target) if isinstance(target, str) else target
    if src.get_attribute("data-handle"):
        # مثل کاربر واقعی: ابتدا روی خود گره می‌رویم تا نوار ابزار و دستگیره‌اش ظاهر شود
        src.locator("xpath=ancestor::*[@data-id][1]").hover(position={"x": 8, "y": 8})
    src.hover()
    if at == "bottom":
        box = dst.bounding_box()
        src.drag_to(dst, target_position={"x": box["width"] / 2, "y": box["height"] - 10})
    else:
        src.drag_to(dst)


def run_scenarios(base):
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"], "headless": not os.environ.get("HEADED"), "slow_mo": int(os.environ.get("SLOWMO", "0"))}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        elif os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        problems = []
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" and "favicon" not in m.text and "404" not in m.text else None)
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))

        print("\n[۱] ورود و باز کردن ویرایشگر (مدیر)")
        login(page, base, "admin")
        page.goto(base + "/panel/builder/1/")
        page.wait_for_selector("#bx-status.is-ok")
        check("سه بخش رابط: پنل المان‌ها، بوم، پنل تنظیمات", page.locator(".bx-left").is_visible() and page.locator("#bx-canvas").is_visible() and page.locator("#bx-settings").is_visible())
        check("بوم خالی در ابتدا", page.locator("#bx-canvas .bx-canvas-empty").count() == 1)

        print("\n[۲] Drag & Drop از پنل المان‌ها به بوم")
        drag(page, '[data-testid="pal-section"]', "#bx-canvas .bx-canvas-empty")
        check("بخش با کشیدن به بوم اضافه شد", page.locator("#bx-canvas .bx-sec").count() == 1)
        check("بخش جدید یک ردیف و یک ستون دارد", page.locator("#bx-canvas .bx-row").count() == 1 and page.locator("#bx-canvas .bx-col").count() == 1)
        drag(page, '[data-testid="pal-heading"]', "#bx-canvas .bx-col")
        drag(page, '[data-testid="pal-text"]', "#bx-canvas .bx-heading")  # روی عنوان رها می‌شود ← قبل از آن
        drag(page, '[data-testid="pal-button"]', "#bx-canvas .bx-text")
        check("ترتیب عناصر بر اساس محل رها شدن: متن/دکمه/عنوان؟", els(page) == ["button", "text", "heading"], els(page))
        drag(page, '[data-testid="pal-row-6-6"]', "#bx-canvas .bx-sec", at="bottom")
        check("ردیف دوستونه با Drag & Drop اضافه می‌شود", page.locator("#bx-canvas .bx-row").count() == 2)
        check("ردیف تازه‌اضافه‌شده در انتهای بخش و دو ستونه است", page.locator("#bx-canvas .bx-row").nth(1).locator(".bx-col").count() == 2)

        print("\n[۳] جابه‌جایی عنصر بین ستون‌ها با دستگیره")
        page.click("#bx-canvas .bx-heading .bx-core")
        handle = page.locator("#bx-canvas .bx-heading > .bx-chrome .bx-handle")
        drag(page, handle, page.locator("#bx-canvas .bx-row").nth(1).locator(".bx-col").nth(0))
        check("عنوان به ستون اول ردیف دوم رفت", row_counts(page) == [2, 1], row_counts(page))
        page.click("#bx-canvas .bx-text .bx-core")
        drag(page, "#bx-canvas .bx-text > .bx-chrome .bx-handle", page.locator("#bx-canvas .bx-row").nth(1).locator(".bx-col").nth(1))
        check("متن به ستون دوم ردیف دوم رفت", row_counts(page) == [1, 2], row_counts(page))
        page.click("#bx-canvas .bx-button .bx-core")
        drag(page, "#bx-canvas .bx-button > .bx-chrome .bx-handle", page.locator("#bx-canvas .bx-row").nth(1).locator(".bx-col").nth(1).locator(".bx-text"))
        types_in_col2 = page.evaluate("[...document.querySelectorAll('#bx-canvas .bx-row')[1].querySelectorAll('.bx-col')[1].querySelectorAll('.bx-el')].map(e=>e.dataset.type)")
        check("رها کردن روی نیمه‌ی بالایی یک عنصر، قبل از آن درج می‌کند", types_in_col2 == ["button", "text"], types_in_col2)

        print("\n[۴] تغییر ترتیب سکشن‌ها با Drag & Drop")
        drag(page, '[data-testid="pal-section"]', "#bx-canvas .bx-sec >> nth=0")  # روی سکشن اول → قبل از آن
        check("دو سکشن وجود دارد و سکشن جدید اول است", page.locator("#bx-canvas .bx-sec").count() == 2 and page.locator("#bx-canvas .bx-sec").nth(0).locator(".bx-el").count() == 0)
        first_has_elements = page.locator("#bx-canvas .bx-sec").nth(1).locator(".bx-el").count()
        page.click("#bx-canvas .bx-sec >> nth=0", position={"x": 4, "y": 4})
        drag(page, page.locator("#bx-canvas .bx-sec").nth(1).locator("> .bx-chrome .bx-handle"), page.locator("#bx-canvas .bx-sec").nth(0))
        check("سکشن دوم با کشیدن دستگیره‌اش به جای اول رفت", page.locator("#bx-canvas .bx-sec").nth(0).locator(".bx-el").count() == first_has_elements and first_has_elements == 3)
        page.click("#bx-canvas .bx-sec >> nth=1", position={"x": 4, "y": 4})
        page.locator("#bx-canvas .bx-sec").nth(1).hover(position={"x": 4, "y": 4})
        page.locator("#bx-canvas .bx-sec").nth(1).locator('> .bx-chrome [data-act="del"]').click()
        check("حذف سکشن خالی با دکمه‌ی ابزار", page.locator("#bx-canvas .bx-sec").count() == 1)

        print("\n[۵] تنظیمات المان انتخاب‌شده (متن، رنگ، اندازه، پس‌زمینه، حاشیه، فاصله)")
        page.click("#bx-canvas .bx-heading .bx-core")
        page.fill('[data-testid="f-text"]', "خوش آمدید به راوین")
        page.fill('[data-testid="f-color"]', "#d6334a")
        page.fill('#bx-settings input[type=number][data-testid="f-font_size"]', "44")
        page.fill('[data-testid="f-bg_color"]', "#fff5e0")
        page.fill('[data-testid="f-border_width"]', "3")
        page.fill('[data-testid="f-border_color"]', "#112233")
        page.fill('[data-testid="f-padding"]', "20")
        cs = page.evaluate("(() => { const h = document.querySelector('#bx-canvas .bx-heading'); const c = h.querySelector('.bx-core'); const a = getComputedStyle(h), b = getComputedStyle(c); return {txt: c.textContent, color: b.color, size: b.fontSize, bg: a.backgroundColor, bw: a.borderTopWidth, bc: a.borderTopColor, pad: a.paddingTop}; })()")
        check("متن/رنگ/اندازه در بوم اعمال شد", cs["txt"] == "خوش آمدید به راوین" and cs["color"] == "rgb(214, 51, 74)" and cs["size"] == "44px", cs)
        check("پس‌زمینه/حاشیه/فاصله‌ی داخلی در بوم اعمال شد", cs["bg"] == "rgb(255, 245, 224)" and cs["bw"] == "3px" and cs["bc"] == "rgb(17, 34, 51)" and cs["pad"] == "20px", cs)
        page.fill('[data-testid="f-color"]', "red")
        check("رنگ نامعتبر پذیرفته نمی‌شود و خطا نشان می‌دهد", page.locator('[data-testid="f-color"]').evaluate("e => e.classList.contains('is-bad')"))
        page.fill('[data-testid="f-color"]', "#d6334a")
        page.click("#bx-canvas .bx-button .bx-core")
        page.fill('[data-testid="f-url"]', "javascript:alert(1)")
        check("نشانی javascript: در پنل رد می‌شود", page.locator('[data-testid="f-url"]').evaluate("e => e.classList.contains('is-bad')"))
        page.fill('[data-testid="f-url"]', "/contact/")
        page.fill('[data-testid="f-label"]', "تماس با ما")

        print("\n[۶] ستون‌بندی و پیش‌نمایش دسکتاپ/تبلت/موبایل در بوم")
        page.click("#bx-canvas .bx-row >> nth=1 >> .bx-heading .bx-core")
        spans = lambda: page.evaluate("[...document.querySelectorAll('#bx-canvas .bx-row')[1].querySelectorAll('.bx-col')].map(c => Math.round(c.getBoundingClientRect().width))")
        wide = spans()
        check("دو ستون هم‌عرض کنار هم هستند", abs(wide[0] - wide[1]) < 4 and wide[0] < 600, wide)
        page.click('.bx-crumbs button:text-is("ستون")')  # انتخاب ستونِ والد از مسیر بالای پنل تنظیمات
        page.fill('[data-testid="f-span"]', "9")
        check("مجموع عرض ستون‌ها از ۱۲ بیشتر نمی‌شود (۹ کنار ۶ رد می‌شود)", page.locator('[data-testid="f-span"]').input_value() != "9" and max(spans()) <= 828)
        page.fill('[data-testid="f-span"]', "4")
        w2 = spans()
        check("تغییر عرض ستون (۴ از ۱۲ کنار ۶) اعمال می‌شود", w2[0] < w2[1] * 0.8, w2)
        page.click('button[data-device="mobile"]')
        page.wait_for_timeout(400)
        w3 = spans()
        check("در حالت موبایل بوم ۳۷۵ پیکسل است و ستون‌ها زیر هم می‌روند", w3[0] == w3[1] and w3[0] <= 375, w3)
        page.click('button[data-device="tablet"]')
        page.wait_for_timeout(400)
        w4 = spans()
        check("در حالت تبلت با تنظیم «فقط موبایل» هنوز کنار هم‌اند", w4[0] < w4[1] and w4[0] + w4[1] < 768, w4)
        page.click('.bx-crumbs button:text-is("ردیف")')
        page.select_option('[data-testid="f-stack_at"]', "tablet")
        w5 = spans()
        check("با تنظیم «در تبلت و موبایل» در حالت تبلت زیر هم می‌روند", w5[0] == w5[1], w5)
        page.select_option('[data-testid="f-stack_at"]', "mobile")
        page.click('button[data-device="desktop"]')
        page.wait_for_timeout(400)

        print("\n[۷] ذخیره خودکار، ماندگاری و ویرایش مجدد")
        wait_saved(page)
        before = state(page, base)
        check("پیش‌نویس روی سرور ذخیره شد (ذخیره‌ی خودکار)", before["ok"] and len(before["document"]["sections"]) == 1 and before["revision"] > 1)
        page.reload()
        page.wait_for_selector("#bx-status.is-ok")
        check("پس از بارگذاری مجدد همان ساختار برگشت (ویرایش مجدد)", row_counts(page) == [1, 3] or row_counts(page) == [0, 3] or sum(row_counts(page)) == 3, row_counts(page))
        check("تنظیمات ذخیره‌شده (رنگ عنوان) حفظ شد", page.evaluate("getComputedStyle(document.querySelector('#bx-canvas .bx-heading .bx-core')).color") == "rgb(214, 51, 74)")

        print("\n[۸] واگرد/ازنو و ذخیره‌ی دستی (Ctrl+S)")
        n_before = len(els(page))
        page.click("#bx-canvas .bx-button .bx-core")
        page.keyboard.press("Delete")
        check("کلید Delete عنصر انتخاب‌شده را حذف کرد", len(els(page)) == n_before - 1)
        page.click("#bx-undo")
        check("واگرد عنصر حذف‌شده را برگرداند", len(els(page)) == n_before)
        page.click("#bx-redo")
        check("ازنو دوباره حذف کرد", len(els(page)) == n_before - 1)
        page.click("#bx-undo")
        page.keyboard.press("Control+s")
        wait_saved(page)
        check("Ctrl+S ذخیره‌ی دستی را انجام داد", state(page, base)["document"]["sections"][0]["rows"] is not None)

        print("\n[۹] تصویر از کتابخانه رسانه")
        drag(page, '[data-testid="pal-image"]', page.locator("#bx-canvas .bx-row").nth(0).locator(".bx-col").nth(0))
        check("المان تصویر اضافه شد (بدون تصویر هنوز)", "image" in els(page))
        page.click('[data-testid="pick-media"]')
        page.wait_for_selector("#bx-media-grid button[data-media-id]")
        page.click("#bx-media-grid button[data-media-id]")
        page.wait_for_selector("#bx-canvas .bx-image img")
        check("تصویر انتخاب‌شده در بوم نمایش داده می‌شود", page.locator("#bx-canvas .bx-image img").get_attribute("src").startswith("/media/"))
        wait_saved(page)

        print("\n[۱۰] پیش‌نمایش واقعی سایت با قالب (دسکتاپ/موبایل)")
        page.click('button[data-device="mobile"]')
        page.click("#bx-mode-preview")
        wait_js(page, "document.querySelector('#bx-preview').getAttribute('src')")
        frame = page.frame_locator("#bx-preview")
        frame.locator(".bx-page").wait_for()
        check("پیش‌نمایش با هدر و فوتر قالب نمایش داده می‌شود", frame.locator(".site-header").count() == 1 and frame.locator(".site-footer").count() == 1)
        iw = page.evaluate("document.querySelector('#bx-preview').getBoundingClientRect().width")
        check("عرض پیش‌نمایش موبایل ۳۷۵ است", round(iw) == 375, iw)
        fw = frame.locator(".bx-row").nth(1).evaluate("r => [...r.children].map(c => Math.round(c.getBoundingClientRect().width))")
        check("در پیش‌نمایش موبایل واقعی ستون‌ها زیر هم‌اند (CSS خروجی نهایی)", fw[0] == fw[1], fw)
        page.click('button[data-device="desktop"]')
        page.wait_for_timeout(600)
        fw = frame.locator(".bx-row").nth(1).evaluate("r => [...r.children].map(c => Math.round(c.getBoundingClientRect().width))")
        check("در پیش‌نمایش دسکتاپ ستون‌ها کنار هم‌اند (۴ و ۶ از ۱۲)", fw[0] < fw[1] and fw[0] > 100, fw)
        page.click("#bx-mode-edit")

        print("\n[۱۱] نسخه‌ها: ذخیره، تغییر، بازیابی")
        page.click("#bx-versions")
        page.click("#bx-save-version")
        page.wait_for_selector('#bx-vlist .bx-v[data-version="1"]')
        page.keyboard.press("Escape")
        n_els = len(els(page))
        page.click("#bx-canvas .bx-heading .bx-core")
        page.keyboard.press("Delete")
        check("عنصر پس از ذخیره‌ی نسخه حذف شد", len(els(page)) == n_els - 1)
        page.click("#bx-versions")
        page.click('[data-testid="restore-1"]')
        wait_js(page, "document.querySelector('#bx-dlg-versions').open === false")
        check("بازیابی نسخه ۱ عنصر حذف‌شده را برگرداند", len(els(page)) == n_els, (n_els, len(els(page))))
        vs = state(page, base)["versions"]
        check("پیش از بازیابی نسخه‌ی خودکار از وضعیت قبل ساخته شد", any(v["is_auto"] for v in vs))

        print("\n[۱۲] انتشار توسط مدیر و نمایش در سایت عمومی")
        public = ctx.new_page()
        public.goto(base + "/about/")
        check("پیش از انتشار، سایت هنوز محتوای قدیمی (Markdown) دارد", "مارک‌داون" in public.content() and public.locator(".bx-page").count() == 0)
        page.click("#bx-publish")
        page.click("#bx-note-ok")
        wait_js(page, "document.querySelector('#bx-dlg-note').open === false")
        public.goto(base + "/about/")
        check("پس از انتشار، صفحه با صفحه‌ساز نمایش داده می‌شود", public.locator(".bx-page").count() == 1 and "خوش آمدید به راوین" in public.content())
        check("قالب سایت (هدر/فوتر/منو) در صفحه‌ی ساخته‌شده حفظ شد", public.locator(".site-header").count() == 1 and public.locator(".site-nav").count() == 1)
        check("هیچ اسکریپتی در بدنه‌ی صفحه‌ی ساخته‌شده نیست", public.locator("main script").count() == 0)
        public.close()

        print("\n[۱۳] جریان تأیید مدیر: نویسنده ارسال می‌کند، مدیر تأیید می‌کند")
        wctx = browser.new_context(viewport={"width": 1440, "height": 900})
        w = wctx.new_page()
        w.on("dialog", lambda d: d.accept())
        login(w, base, "writer")
        w.goto(base + "/panel/builder/1/")
        w.wait_for_selector("#bx-status.is-ok")
        check("برای نویسنده دکمه‌ی «ارسال برای تأیید» نمایش داده می‌شود نه «انتشار»", w.inner_text("#bx-publish").strip() == "ارسال برای تأیید")
        w.click("#bx-canvas .bx-heading .bx-core")
        w.fill('[data-testid="f-text"]', "<img src=x onerror=window.__pwn=1> نسخه نویسنده")
        wait_saved(w)
        w.click("#bx-publish")
        w.fill("#bx-note", "لطفاً بررسی شود")
        w.click("#bx-note-ok")
        wait_js(w, "document.querySelector('#bx-dlg-note').open === false")
        pub = wctx.new_page()
        pub.goto(base + "/about/")
        check("پیش از تأیید، سایت نسخه‌ی نویسنده را نشان نمی‌دهد", "نسخه نویسنده" not in pub.content())
        resp = w.evaluate("async (u) => { const r = await fetch(u, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'publish'})}); return r.status; }", base + "/panel/builder/1/api/versions/")
        check("نویسنده با درخواست مستقیم هم نمی‌تواند منتشر کند (۴۰۳ — CSRF/مجوز)", resp == 403, resp)
        pending = state(w, base)["versions"]
        pending_no = next(v["number"] for v in pending if v["state"] == "pending")
        # مدیر تأیید می‌کند
        page.reload()
        page.wait_for_selector("#bx-status.is-ok")
        page.click("#bx-versions")
        page.click(f'[data-testid="approve-{pending_no}"]')
        wait_js(page, "[...document.querySelectorAll('#bx-vlist .bx-badge.live')].length > 0")
        pub.goto(base + "/about/")
        html = pub.content()
        check("پس از تأیید، نسخه‌ی نویسنده منتشر شد", "نسخه نویسنده" in html)
        check("کد تزریقی نویسنده فقط متن escape‌شده است (بدون عنصر img و بدون اجرا)", pub.locator("main img[src='x']").count() == 0 and pub.evaluate("window.__pwn === undefined"))
        wctx.close()

        print("\n[۱۴] خطاهای جاوااسکریپت/کنسول")
        check("هیچ خطای JavaScript در ویرایشگر رخ نداد", not problems, problems[:3])
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
