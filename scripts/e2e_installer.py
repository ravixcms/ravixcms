"""اجرای واقعی (بدون mock) نصب‌کننده با افزونهٔ نمونه: keygen → sign → نصب از پنل → فعال‌سازی → ارتقا → بازگشت.
اجرا: python scripts/e2e_installer.py   (همه‌چیز در پوشهٔ موقت؛ کلیدها بعد از اجرا پاک می‌شوند)"""
import io, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="ravin-e2e-"))
env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.development", "RAVIN_PLUGIN_WEB_INSTALL": "true",
       "RAVIN_PLUGIN_INSTALL_DIR": str(tmp / "installed"), "RAVIN_PLUGIN_BACKUP_DIR": str(tmp / "backups"),
       "DATABASE_URL": "sqlite:///e2e_tmp.sqlite3"}  # مسیر sqlite نسبت به پروژه است؛ در پایان پاک می‌شود
py = sys.executable
run = lambda *a, **k: subprocess.run([py, "manage.py", *a], cwd=ROOT, env={**env, **k.pop("extra_env", {})}, capture_output=True, text=True, check=k.pop("check", True))

def build_zip(version):
    """افزونهٔ حداقلی e2e_hello (نه نمونه‌های مخزن، چون آن‌ها از قبل در plugins/ هستند و نصب‌کننده بازنویسی نمی‌کند)."""
    manifest = {"slug": "e2e_hello", "name": "سلام", "version": version, "description": "e2e", "api_version": 1, "ravin": ">=0.5.0,<1.0.0",
                "public_routes": [{"path": "", "view": "views:home"}]}
    views = f"from django.http import HttpResponse\n\n\ndef home(request, api):\n    return HttpResponse('hello {version}')\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("e2e_hello/plugin.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("e2e_hello/__init__.py", "")
        zf.writestr("e2e_hello/views.py", views)
    return buf.getvalue()

def step(msg, ok=True):
    print(("✔ " if ok else "✘ ") + msg)
    if not ok:
        sys.exit(1)

run("migrate", "-v0")
keyfile = tmp / "dev.key"
out = run("plugin_keygen", "--out", str(keyfile)).stdout
pub = re.search(r"کلید عمومی[^:]*: (\S+)", out).group(1)
step("keygen: کلید خصوصی 600 و چاپ‌نشده", oct(keyfile.stat().st_mode & 0o777) == "0o600" and keyfile.read_text().strip() not in out)
env["RAVIN_TRUSTED_PUBLIC_KEYS"] = f"dev:{pub}"

def sign(version):
    z = tmp / f"cf-{version}.zip"
    z.write_bytes(build_zip(version))
    o = run("plugin_sign", str(z), "--key-file", str(keyfile)).stdout
    return z, re.search(r"signature: (\S+)", o).group(1), re.search(r"key_id: (\S+)", o).group(1)

script = f'''
import django; django.setup()
from django.test import Client
from django.contrib.auth import get_user_model
from apps.plugins.models import PluginRecord, PluginBackup
from apps.plugins.integrity import fingerprint
from apps.plugins import services
from django.conf import settings
U = get_user_model()
admin = U.objects.create_user("e2eadmin", "a@b.com", "Str0ng-Pass-9x!", role="admin")
c = Client(); c.force_login(admin)
def upload(path, sig, kid, upgrade=False):
    with open(path, "rb") as f:
        return c.post("/panel/plugins/install-package/", {{"op": "local", "package": f, "signature": sig, "key_id": kid, **({{"upgrade": "on"}} if upgrade else {{}})}}, HTTP_HOST="localhost")
import json, sys
z1, s1, k1 = json.loads(sys.argv[1])
r = upload(z1, s1, k1)
print("UPLOAD", r.status_code, r["Location"]); from django.contrib.messages import get_messages; print("MSG", [str(m) for m in get_messages(r.wsgi_request)])
d = settings.RAVIN_PLUGIN_INSTALL_DIR / "e2e_hello"
print("PLACED", d.is_dir(), "RECORD", PluginRecord.objects.filter(slug="e2e_hello").exists())
services.install("e2e_hello", admin, fingerprint(d)); services.activate("e2e_hello", admin)
print("ACTIVE", PluginRecord.objects.get().is_active)
r = c.get("/ext/e2e_hello/", HTTP_HOST="localhost"); print("PUBLIC", r.status_code, r.content)
z2, s2, k2 = json.loads(sys.argv[2])
r = upload(z2, s2, k2, True); print("UPGRADE", r.status_code, r["Location"])
rec = PluginRecord.objects.get(); print("AFTER_UPGRADE", rec.version, rec.is_active, services.get_row("e2e_hello").status)
services.approve_update("e2e_hello", admin, fingerprint(d)); services.activate("e2e_hello", admin)
print("V2", PluginRecord.objects.get().version, c.get("/ext/e2e_hello/", HTTP_HOST="localhost").content)
b = PluginBackup.objects.get()
r = c.post(f"/panel/plugins/e2e_hello/rollback/{{b.pk}}/", {{"confirm_data_loss": "on"}}, HTTP_HOST="localhost")
rec = PluginRecord.objects.get(); print("ROLLBACK", rec.version, rec.is_active, fingerprint(d) == rec.fingerprint)
bad = open(z2, "rb").read() + b"x"
open(z2, "wb").write(bad)
r = upload(z2, s2, k2, True); print("TAMPERED", r["Location"], PluginRecord.objects.get().version)
'''
z1, s1, k1 = sign("1.0.0")
z2, s2, k2 = sign("1.0.1")
res = subprocess.run([py, "-c", script, json.dumps([str(z1), s1, k1]), json.dumps([str(z2), s2, k2])], cwd=ROOT, env=env, capture_output=True, text=True)
print(res.stdout, res.stderr[-1500:] if res.returncode else "")
shutil.rmtree(tmp, ignore_errors=True)
(ROOT / "e2e_tmp.sqlite3").unlink(missing_ok=True)
