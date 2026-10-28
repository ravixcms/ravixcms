#!/usr/bin/env python3
"""
End-to-end test of Ravix Studio in a real browser (Playwright + Chromium): the editor, drag & drop, keyboard use,
autosave, conflicts, the approval workflow, publishing, and the public page.

It creates a throw-away database, seeds it, starts the dev server on a free port, runs the scenarios and cleans up.
One-time setup:  pip install playwright && playwright install chromium
Run:             python scripts/e2e_studio.py            (HEADED=1 to watch, SLOWMO=300 to slow down, CHROMIUM_PATH for a custom browser)
Exit code 0 when every check passes.
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
PY = os.environ.get("RAVIX_PYTHON") or sys.executable
PASSWORD = "Pass12345!xyz"
results = []


def wait_js(page, expr, timeout=8000):
    """Poll an expression with CDP evaluate (wait_for_function would use eval, which the panel's CSP rightly blocks)."""
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
from apps.content.models import Page, Post
from apps.media_library.models import Media
from apps.core import modules
modules.set_enabled("studio", True)
U = get_user_model()
a = U.objects.create_user("admin", "a@x.com", "%(pw)s", role=Role.ADMIN)
w = U.objects.create_user("writer", "w@x.com", "%(pw)s", role=Role.AUTHOR)
Page.objects.create(title="درباره ما", slug="about", body="متن قدیمی", status="published", author=a)
Post.objects.create(title="نوشتهٔ آزمون", slug="e2e-post", body="x", status="draft", author=w)
buf = io.BytesIO(); Image.new("RGB", (640, 400), (200, 60, 60)).save(buf, "PNG")
m = Media(original_name="e2e-test.png", title="تصویر آزمون", alt_text="تصویر قرمز", kind="image", mime_type="image/png", size=buf.tell(), width=640, height=400, uploaded_by=a)
m.file.save("e2e-test.png", ContentFile(buf.getvalue()), save=True)
print("seeded")
''' % {"pw": PASSWORD}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravix-e2e-"))
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
        for f in glob.glob(str(ROOT / "media" / "**" / "*"), recursive=True):
            if os.path.isfile(f) and os.path.getmtime(f) >= started - 1:
                os.remove(f)
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + ("" if not failed else "; failed: " + ", ".join(failed)))
    return 1 if failed else 0


# ---------------------------------------------------------------------------------------------- helpers
def login(page, base, user):
    page.goto(base + "/accounts/login/")
    page.fill("#id_username", user)
    page.fill("#id_password", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_url(lambda u: "/accounts/login" not in u)


def api(page, url, method="GET", body=None):
    return page.evaluate(
        """async ([u, m, b, csrf]) => { const o = {method: m, credentials: 'same-origin', headers: {'Accept': 'application/json'}};
        if (b !== null) { o.headers['Content-Type'] = 'application/json'; o.headers['X-CSRFToken'] = csrf; o.body = JSON.stringify(b); }
        const r = await fetch(u, o); const j = await r.json(); j.status = r.status; return j; }""",
        [url, method, body, page.evaluate("() => JSON.parse(document.getElementById('studio-config').textContent).csrf")])


def open_editor(page, base, kind="page", pk=1):
    page.goto(f"{base}/panel/studio/{kind}/{pk}/")
    wait_js(page, "document.getElementById('stu') && document.getElementById('stu').dataset.state === 'ready'")
    wait_js(page, "document.getElementById('stu-frame').contentDocument && document.getElementById('stu-frame').contentDocument.getElementById('st-mount')")
    time.sleep(0.6)


def doc_state(page):
    cfg_url = page.evaluate("() => JSON.parse(document.getElementById('studio-config').textContent).urls.state")
    return api(page, cfg_url)


def preview(page):
    return page.frame_locator("#stu-frame")


def wait_saved(page, timeout=10000):
    wait_js(page, "document.getElementById('stu-status').getAttribute('data-kind') === 'saved'", timeout)


def count_in_preview(page, kind):
    return page.evaluate("(k) => document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=\"' + k + '\"]').length", kind)


def mouse_drag(page, source, target, steps=14):
    """A slow, real mouse drag (SortableJS needs intermediate mouse moves; drag_to jumps straight to the target)."""
    a, b = source.bounding_box(), target.bounding_box()
    x0, y0 = a["x"] + min(10, a["width"] / 2), a["y"] + a["height"] / 2
    x1, y1 = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
    time.sleep(0.3)
    page.mouse.up()


def run_scenarios(base):
    with sync_playwright() as p:
        launch = {"args": ["--no-sandbox"], "headless": not os.environ.get("HEADED"), "slow_mo": int(os.environ.get("SLOWMO", "0"))}
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        problems = []
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" and "favicon" not in m.text and "404" not in m.text and "409" not in m.text else None)

        print("\n[1] Opening the editor as administrator")
        login(page, base, "admin")
        open_editor(page, base)
        check("three areas: widgets, preview, settings", page.locator(".stu-left").is_visible() and page.locator("#stu-frame").is_visible() and page.locator(".stu-right").is_visible())
        check("the preview is the real theme page (has the site header)", preview(page).locator("header, .site-header, nav").count() > 0)
        check("no console errors on load", not problems, problems)

        print("\n[2] Adding structure by click and drag & drop")
        page.locator(".stu-presets button").nth(1).click()  # two columns
        wait_js(page, "document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=column]').length === 2")
        check("a two-column section appears in the preview", count_in_preview(page, "section") == 1 and count_in_preview(page, "column") == 2)
        page.locator('[data-widget="heading"]').click()
        wait_js(page, "document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=widget]').length === 1")
        check("click adds the widget to the selected column", count_in_preview(page, "widget") == 1)
        col2 = preview(page).locator('[data-st-kind="column"]').nth(1)
        page.locator('[data-widget="text"]').drag_to(col2)
        wait_js(page, "document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=widget]').length === 2", 8000)
        types = page.evaluate("[...document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=column]')].map(c => [...c.querySelectorAll(':scope > [data-st-kind=widget]')].map(w => w.dataset.stType))")
        check("dragging from the palette into a column drops it there", types == [["heading"], ["text"]], types)

        print("\n[3] Reordering in the preview and in the Layers panel")
        h = preview(page).locator('[data-st-type="heading"]')
        h.hover()
        handle = h.locator(":scope > .st-handle")
        mouse_drag(page, handle, preview(page).locator('[data-st-kind="column"]').nth(1).locator('[data-st-type="text"]'))
        wait_js(page, "(() => { const c = document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=column]'); return c[0].querySelectorAll('[data-st-kind=widget]').length === 0; })()")
        types = page.evaluate("[...document.getElementById('stu-frame').contentDocument.querySelectorAll('[data-st-kind=column]')].map(c => [...c.querySelectorAll(':scope > [data-st-kind=widget]')].map(w => w.dataset.stType))")
        check("a widget dragged by its handle moves to another column", types == [[], ["heading", "text"]] or types == [[], ["text", "heading"]], types)
        page.click("#tab-layers")
        items = page.locator('#stu-tree [role="treeitem"]')
        check("Layers shows section, columns and widgets", items.count() == 5, items.count())
        sel = page.locator('#stu-tree [role="treeitem"][data-kind="widget"]').first
        sel.focus()
        before = page.evaluate("[...document.querySelectorAll('#stu-tree [data-kind=widget]')].map(x => x.getAttribute('aria-label'))")
        page.keyboard.press("Alt+ArrowDown")
        time.sleep(0.4)
        after = page.evaluate("[...document.querySelectorAll('#stu-tree [data-kind=widget]')].map(x => x.getAttribute('aria-label'))")
        check("Alt+Down moves the item (keyboard only)", before != after and sorted(before) == sorted(after), (before, after))

        print("\n[4] Editing settings, autosave, undo/redo")
        page.locator('#stu-tree [data-kind="widget"]').first.click()
        field = page.locator("#stu-inspector input[type=text]").first
        field.fill("عنوان آزمایشی E2E")
        wait_js(page, "(document.getElementById('stu-frame').contentDocument.body.textContent || '').includes('عنوان آزمایشی E2E') || (document.getElementById('stu-frame').contentDocument.body.textContent || '').includes('E2E')", 8000)
        check("typing in a setting updates the preview", "E2E" in (preview(page).locator("#st-mount").inner_text()))
        wait_saved(page)
        st = doc_state(page)
        texts = json.dumps(st["document"], ensure_ascii=False)
        check("autosave stored the draft on the server", "E2E" in texts)
        page.keyboard.press("Escape")
        page.locator("#stu-undo").click()
        wait_js(page, "!(document.getElementById('stu-frame').contentDocument.getElementById('st-mount').textContent || '').includes('E2E')")
        check("undo reverts the change", "E2E" not in preview(page).locator("#st-mount").inner_text())
        page.locator("#stu-redo").click()
        wait_js(page, "(document.getElementById('stu-frame').contentDocument.getElementById('st-mount').textContent || '').includes('E2E')")
        check("redo re-applies it", True)
        wait_saved(page)

        print("\n[5] Tablet / mobile settings are separate")
        page.locator('[data-device="tablet"]').click()
        check("the preview frame narrows for tablet", 700 < page.locator("#stu-frame").bounding_box()["width"] < 900)
        page.locator("#stu-tree li[data-kind=section] > .stu-node").first.click()
        page.locator('#stu-right-tabs button', has_text="Style").first.click() if page.locator('#stu-right-tabs button', has_text="Style").count() else page.locator("#stu-right-tabs button").nth(1).click()
        bg = page.locator('#stu-inspector input[type=text][placeholder*="#"]').first
        bg.fill("#112233")
        wait_saved(page)
        d = doc_state(page)["document"]["sections"][0]["style"]
        check("the value is stored under tablet only", d.get("tablet", {}) and "desktop" not in d, d)
        page.locator('[data-device="desktop"]').click()

        print("\n[6] Reload keeps the draft; versions and publishing")
        open_editor(page, base)
        check("after a reload the page structure is still there", count_in_preview(page, "section") == 1)
        page.locator("#stu-save-version").click()
        page.locator("#stu-dialog .stu-primary, #stu-dialog-foot .stu-primary").first.click()
        time.sleep(0.8)
        page.locator("#stu-publish").click()
        page.locator("#stu-dialog-foot .stu-primary").first.click()
        time.sleep(1.0)
        st = doc_state(page)
        check("publishing makes a version live", st["live"] is not None, st.get("live"))
        r = page.request.get(base + "/en/about/")
        html = r.text()
        check("the public page shows the Studio content", "E2E" in html and r.status == 200)
        check("no editor script leaks into the public page", "studio/js/editor.js" not in html and "preview.js" not in html and "sortable" not in html.lower())

        print("\n[7] Concurrent editing never silently overwrites")
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        page2 = ctx2.new_page()
        login(page2, base, "admin")
        open_editor(page2, base)
        page.locator("#tab-layers").click()
        page.locator("#stu-tree li[data-kind=section] > .stu-node").first.click()
        page.locator("#stu-insp-actions button[data-action=duplicate]").click()
        wait_saved(page)
        page2.locator('[data-widget="divider"]').click()
        wait_js(page2, "document.querySelector('#stu-dialog[open]')", 12000)
        check("the second editor is warned about the newer draft", page2.locator("#stu-dialog[open]").count() == 1)
        ctx2.close()

        print("\n[8] An author cannot publish or use Custom HTML")
        ctx3 = browser.new_context(viewport={"width": 1440, "height": 900})
        page3 = ctx3.new_page()
        login(page3, base, "writer")
        open_editor(page3, base, "post", 1)
        check("the author sees 'Submit for review' instead of 'Publish'", page3.locator("#stu-publish").get_attribute("data-mode") == "submit")
        check("Custom HTML is disabled for the author", page3.locator('[data-widget="custom_html"][aria-disabled="true"]').count() == 1)
        ctx3.close()

        print("\n[9] Keyboard shortcut and classic editor remain available")
        check("a link to the classic editor is offered", page.locator("#stu-classic").get_attribute("href") is not None)
        page.goto(base + "/panel/studio/")
        check("the Studio list shows the page", page.locator("table.table").count() == 1)

        check("no unexpected console errors during the session", not problems, problems[:3])
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
