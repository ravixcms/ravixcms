#!/usr/bin/env python3
"""
Lighthouse (mobile) on a sample Ravix Studio page: hero, cards, gallery, quote and a click-to-load video.

Creates a throw-away database, seeds the page, starts the server, runs Lighthouse and prints the four scores.
Needs Node (npx) and a Chromium: CHROME_PATH, or the one Playwright installed.
Run:  python scripts/lighthouse_studio.py            (MIN_SCORE=90 by default; exit code 1 when any category is lower)
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

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("RAVIX_PYTHON") or sys.executable
MIN_SCORE = int(os.environ.get("MIN_SCORE", "90"))

SEED = r'''
import io
from PIL import Image
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from apps.accounts.permissions import Role
from apps.content.models import Page
from apps.core import modules
from apps.media_library.models import Media
from apps.studio import services
from apps.studio.fields import field_defaults
from apps.studio.registry import get_widget
from apps.studio.schema import COLUMN_PROPS, SECTION_PROPS, empty_document
modules.set_enabled("studio", True)
admin = get_user_model().objects.create_user("admin", "a@x.com", "Pass12345!xyz", role=Role.ADMIN)
page = Page.objects.create(title="Sample", slug="sample", body="x", status="published", author=admin)
n = [0]
def nid():
    n[0] += 1
    return "n%04d" % n[0]
def w(type_, **props):
    spec = get_widget(type_); p = field_defaults(spec.fields); p.update(props)
    return {"id": nid(), "type": type_, "props": p, "style": {}}
def col(*ws):
    return {"id": nid(), "type": "column", "props": field_defaults(COLUMN_PROPS), "style": {}, "widgets": list(ws)}
def sec(*cs, **props):
    p = field_defaults(SECTION_PROPS); p.update(props)
    return {"id": nid(), "type": "section", "props": p, "style": {}, "columns": list(cs)}
def png(color):
    buf = io.BytesIO(); Image.new("RGB", (900, 600), color).save(buf, "JPEG", quality=70); return buf.getvalue()
medias = []
for i, c in enumerate([(200, 80, 80), (80, 160, 90), (70, 100, 200)]):
    m = Media(original_name="s%d.jpg" % i, kind=Media.Kind.IMAGE, mime_type="image/jpeg", width=900, height=600, alt_text="Sample image %d" % (i + 1), uploaded_by=admin)
    m.file.save("s%d.jpg" % i, ContentFile(png(c)), save=True); medias.append(m)
doc = empty_document()
doc["sections"] = [
    sec(col(w("heading", text="Build pages visually"), w("text", content="Sections, columns and widgets, drawn once and served as plain HTML."), w("button", label="Get started", url="/"))),
    sec(*[col(w("card", title="Card %d" % i, text="A short description of this card.")) for i in (1, 2, 3)]),
    sec(col(w("gallery", images=[{"media": m.pk, "alt": "Sample image"} for m in medias]))),
    sec(col(w("quote", text="A short, memorable quote."))),
    sec(col(w("video", mode="embed", source="https://www.youtube.com/watch?v=dQw4w9WgXcQ", title="Sample video"))),
]
d = services.get_document(page)
services.save_draft(d.pk, doc, d.draft_rev, admin)
services.publish_draft(d.pk, admin)
print("seeded")
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chrome():
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        path = p.chromium.executable_path
    if not os.path.exists(path):  # a different Playwright build than the installed browsers: take any installed Chromium
        import glob
        found = sorted(glob.glob(os.path.join(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright")), "chromium-*", "chrome-linux*", "chrome")))
        if found:
            return found[-1]
    return path


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravix-lh-"))
    started = time.time()
    port = free_port()
    rel = os.path.relpath(tmp / "lh.sqlite3", ROOT)
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + rel, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONUNBUFFERED": "1"}
    server = None
    try:
        subprocess.run([PY, "manage.py", "migrate", "-v0"], cwd=ROOT, env=env, check=True)
        subprocess.run([PY, "manage.py", "shell", "-v0"], cwd=ROOT, env=env, input=SEED.encode(), check=True)
        server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), 0.5).close()
                break
            except OSError:
                time.sleep(0.5)
        out = tmp / "lh.json"
        flags = "--headless=new --no-sandbox --disable-gpu"
        cmd = ["npx", "--yes", "lighthouse@12", f"http://127.0.0.1:{port}/fa/sample/", "--quiet", "--output=json", f"--output-path={out}",
               "--form-factor=mobile", f"--chrome-flags={flags}", "--only-categories=performance,accessibility,best-practices,seo"]
        subprocess.run(cmd, cwd=tmp, env={**os.environ, "CHROME_PATH": chrome()}, check=True)
        cats = json.loads(out.read_text())["categories"]
    finally:
        if server:
            server.terminate()
        for f in (ROOT / "media").rglob("*"):
            if f.is_file() and f.stat().st_mtime >= started - 1:
                f.unlink()
        shutil.rmtree(tmp, ignore_errors=True)
    low = []
    for k, v in cats.items():
        score = round(v["score"] * 100)
        print(f"  {k:15s} {score}")
        if score < MIN_SCORE:
            low.append(k)
    print("OK" if not low else "below %d: %s" % (MIN_SCORE, ", ".join(low)))
    return 1 if low else 0


if __name__ == "__main__":
    sys.exit(main())
