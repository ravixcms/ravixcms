#!/usr/bin/env python3
"""End-to-end check that simulates a cPanel shared host: a copy of the code in ~/ravix, ~/public_html, no root, no Redis, no systemd, and the site
served through passenger_wsgi.py (scripts/passenger_sim.py restarts it on tmp/restart.txt like Passenger and LiteSpeed do).

It walks through the real web installer, then checks the lock, the private files, ravix_cron and the guest page cache.

    python scripts/e2e_shared_hosting.py [--db mysql://user:pass@127.0.0.1:3306/name | --db sqlite] [--mount /site] [--keep]
"""
import argparse
import http.client
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit

SRC = Path(__file__).resolve().parent.parent
PORT = 8765
failures = []


def inside(path, *roots):
    return any(Path(path).resolve().is_relative_to(Path(r).resolve()) for r in roots)


def step(msg, ok=True, detail=""):
    print(("✔ " if ok else "✘ ") + msg + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(msg)
        raise SystemExit(f"FAILED: {msg} {detail}")


class Browser:
    def __init__(self, mount=""):
        self.cookies = {}
        self.mount = mount

    def request(self, method, path, data=None, headers=None, follow=0, host=f"127.0.0.1:{PORT}"):
        headers = dict(headers or {})
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        body = None
        if data is not None:
            body = urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers.setdefault("Referer", f"http://{host}{path}")
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
        started = time.perf_counter()
        conn.request(method, path, body=body, headers={"Host": host, **headers})
        resp = conn.getresponse()
        raw = resp.read()
        elapsed = time.perf_counter() - started
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        for line in resp.msg.get_all("Set-Cookie") or []:
            jar = SimpleCookie()
            jar.load(line)
            for name, morsel in jar.items():
                if morsel.value == "" or morsel["max-age"] == "0":
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        conn.close()
        out = (resp.status, hdrs, raw.decode("utf-8", "replace"), elapsed)
        if follow and resp.status in (301, 302, 303, 307, 308) and "location" in hdrs:
            target = urlsplit(hdrs["location"])
            return self.request("GET", target.path + (("?" + target.query) if target.query else ""), follow=follow - 1, host=host)
        return out

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def csrf(self, html):
        m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
        return m.group(1) if m else ""

    def post_form(self, path, data, page=None, **kw):
        page = page if page is not None else self.get(path)[2]
        return self.request("POST", path, {"csrfmiddlewaretoken": self.csrf(page), **data}, **kw)


def wait_up(browser, tries=120):
    for _i in range(tries):
        try:
            return browser.get(browser.mount + "/healthz")[0]
        except OSError:
            time.sleep(0.5)
    step("the application server started", False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sqlite")
    ap.add_argument("--mount", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--zip", help="install from this ravix-<version>-cpanel.zip (like a real host) instead of a copy of the source tree")
    a = ap.parse_args()
    mount = ("/" + a.mount.strip("/")) if a.mount.strip("/") else ""

    home = Path(tempfile.mkdtemp(prefix="ravix-host-"))
    os.chmod(home, 0o755)
    app = home / "ravix"
    (home / "public_html").mkdir()
    ignore = shutil.ignore_patterns(".git", ".venv", "venv", "node_modules", "__pycache__", "staticfiles", "*.sqlite3", "tmp", "media", ".pytest_cache", "install_token")
    if a.zip:
        import zipfile

        with zipfile.ZipFile(a.zip) as zf:
            zf.extractall(app)
        step("the cPanel package unpacks with the hosting marker and passenger_wsgi.py", (app / "ravix-hosting.marker").is_file() and (app / "passenger_wsgi.py").is_file())
        step("the package has no tests, VPS files or private names", not any((app / x).exists() for x in ("deploy", "docker", "install.sh", "apps/core/tests")))
        env_hosting = {}
    else:
        shutil.copytree(SRC, app, ignore=ignore)
    data = home / "ravix-data"
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "RAVIX_", "DATABASE_URL", "REDIS"))},
           "HOME": str(home), **({} if a.zip else {"RAVIX_HOSTING": "shared"}), "RAVIX_INSTALL_FORCE_HTTPS": "false", "PYTHONDONTWRITEBYTECODE": "1"}
    py = sys.executable
    print(f"host simulation in {home} (python {sys.version_info.major}.{sys.version_info.minor}, uid {os.getuid()})", flush=True)
    step("not running as root", os.getuid() != 0 or os.environ.get("E2E_ALLOW_ROOT") == "1", "run as a normal user")

    sim_args = [py, str(SRC / "scripts" / "passenger_sim.py"), "--root", str(app), "--port", str(PORT)] + (["--mount", mount] if mount else [])
    sim = subprocess.Popen(sim_args, cwd=app, env=env, stdout=open(home / "sim.log", "w"), stderr=subprocess.STDOUT)
    b = Browser(mount)
    try:
        run(b, a, mount, home, app, data, env, py)
    finally:
        sim.send_signal(signal.SIGTERM)
        try:
            sim.wait(10)
        except subprocess.TimeoutExpired:
            sim.kill()
        if not a.keep:
            shutil.rmtree(home, ignore_errors=True)


def manage(app, env, py, *args, check=True):
    p = subprocess.run([py, "manage.py", *args], cwd=app, env=env, capture_output=True, text=True, timeout=900)
    if check and p.returncode != 0:
        raise SystemExit(f"manage.py {' '.join(args)} failed:\n{p.stdout[-800:]}\n{p.stderr[-1500:]}")
    return p


def run(b, a, mount, home, app, data, env, py):
    # -------- 1. setup mode: the installer is the only thing the site shows, and it shows nothing but the code box
    code = wait_up(b)
    step("the application starts without a settings file (setup mode)", code in (200, 302, 404), code)
    st, h, html, _t = b.get(mount + "/", follow=3)
    step("the first visit lands on the installer, which asks for the install code only",
         st == 200 and "XXXX-XXXX-XXXX-XXXX" in html and "csrfmiddlewaretoken" in html and "Database" not in html, st)
    key_file = data / "install-key.txt"
    step("the install code is a private file outside public_html and the code folder",
         key_file.is_file() and stat.S_IMODE(key_file.stat().st_mode) == 0o600 and not inside(key_file, home / "public_html", app))
    key = key_file.read_text().strip()
    for path in ("/install-key.txt", "/ravix-data/install-key.txt", "/../ravix-data/install-key.txt"):
        step(f"the code is not reachable at {path}", key not in b.get(mount + path)[2])
    st, h, html, _t = b.post_form(mount + "/install/", {"code": "AAAA-BBBB-CCCC-DDDD"})
    step("a wrong code is refused", st == 200 and "not correct" in html and "Host check" not in html and "data-status" not in html, st)
    st, h, html, _t = b.post_form(mount + "/install/", {"code": key.lower()})
    step("the right code unlocks the installer", st == 302, st)

    # -------- 2. host checks, database, site
    st, h, html, _t = b.get(mount + "/install/")
    step("the host check page lists the checks and none is an error", st == 200 and 'data-status="ok"' in html and 'data-status="error"' not in html, re.findall(r'data-status="error".{0,300}', html, re.S)[:1])
    b.post_form(mount + "/install/", {"action": "continue"}, page=html)
    page = b.get(mount + "/install/")[2]
    if a.db == "sqlite":
        db_form = {"action": "db", "engine": "sqlite"}
    else:
        u = urlsplit(a.db)
        db_form = {"action": "db", "engine": "mysql", "host": u.hostname, "port": str(u.port or 3306), "name": unquote(u.path.lstrip("/")),
                   "user": unquote(u.username or ""), "password": unquote(u.password or "")}
    bad = dict(db_form)
    if a.db != "sqlite":
        bad["password"] = "definitely-wrong"
        st, h, html, _t = b.post_form(mount + "/install/", bad, page=page)
        step("a wrong database password is explained and does not leak", st == 200 and "definitely-wrong" not in html and "Could not connect" in html, st)
    st, h, html, _t = b.post_form(mount + "/install/", db_form, page=page)
    step("the database connection is accepted", st == 302, st)
    page = b.get(mount + "/install/")[2]
    site = {"action": "site", "site_name": "Host Test", "language": "en", "timezone": "UTC", "username": "owner", "email": "owner@example.com",
            "password1": "Str0ng-Passw0rd-92!", "password2": "Str0ng-Passw0rd-92!"}
    st, h, html, _t = b.post_form(mount + "/install/", site, page=page)
    step("the installation job starts", st == 302, st)

    # -------- 3. progress until done
    deadline = time.time() + 600
    final = ""
    while time.time() < deadline:
        st, h, html, _t = b.get(mount + "/install/")
        if "Ravix is installed" in html:
            final = html
            break
        if "stopped with an error" in html:
            step("the installation finished", False, re.sub(r"<[^>]+>", " ", html)[-600:])
        time.sleep(2)
    step("the installer reports success and shows the cron command", "manage.py ravix_cron" in final and "*/5 * * * *" in final)
    cron = re.search(r"(\*/5 \* \* \* \* [^<]+)", final).group(1)
    step("the cron command points at this app and uses the hosting settings", f"cd {app}" in cron and "config.settings.hosting" in cron, cron)

    # -------- 4. locked for good, private files
    time.sleep(2)
    step("the lock file exists", (data / "installed.lock").is_file())
    step("the install code was deleted", not key_file.exists())
    envf = data / "ravix.env"
    step("the settings file is private (600) and outside public_html and the code folder",
         envf.is_file() and stat.S_IMODE(envf.stat().st_mode) == 0o600 and not inside(envf, home / "public_html", app))
    text = envf.read_text()
    step("the settings file holds the generated secrets", "DJANGO_SECRET_KEY=" in text and "RAVIX_SECRETS_KEY=" in text and "Str0ng-Passw0rd" not in text)
    step("the job file with the passwords is gone", not list((data / "setup-jobs").glob("job-*.json")))
    b2 = Browser(mount)
    time.sleep(1.5)  # the simulator restarts the application (tmp/restart.txt)
    wait_up(b2)
    st = b2.get(mount + "/install/")[0]
    step("the installer is closed for good (404)", st == 404, st)
    step("the installer stays closed for the browser that ran it too", b.get(mount + "/install/")[0] == 404)

    # -------- 5. the installed site
    st, h, html, _t = b2.get(mount + "/", follow=4)
    step("the home page works after the restart", st == 200 and "Host Test" in html, st)
    if mount:
        step("links carry the sub-folder prefix", f'href="{mount}/' in html or f"href='{mount}/" in html)
    login = b2.get(mount + "/accounts/login/")
    st, h, html, _t = b2.post_form(mount + "/accounts/login/", {"username": "owner", "password": "Str0ng-Passw0rd-92!"}, page=login[2])
    step("the administrator can sign in", st == 302 and "sessionid" in b2.cookies, st)
    st, h, html, _t = b2.get(mount + "/panel/", follow=2)
    step("the panel opens", st == 200, st)
    manage(app, env, py, "shell", "-c", "from apps.core import modules; modules.set_enabled('studio', True)")  # modules are switched on from the panel; here the same call
    st, h, html, _t = b2.get(mount + "/panel/studio/", follow=2)
    step("Ravix Studio opens in hosting mode", st == 200, st)
    st = b2.get(mount + "/static/studio/studio.css")[0]
    step("Studio's static files are served (WhiteNoise)", st == 200, st)
    for path in ("/ravix.env", "/.env", "/db.sqlite3", "/static/../ravix.env", "/media/../../ravix-data/ravix.env", "/ravix-data/db/ravix.sqlite3", "/manage.py", "/config/settings/hosting.py"):
        got = b2.get(mount + path)
        step(f"{path} is not served", got[0] in (404, 400, 301, 302) and "SECRET_KEY" not in got[2] and "DJANGO_" not in got[2], got[0])
    step("DEBUG is off (a 404 page shows no debug details)", "Using the URLconf" not in b2.get(mount + "/no-such-page-xyz")[2])

    # -------- 6. cron
    first = manage(app, env, py, "ravix_cron", "--no-keep-alive")
    step("ravix_cron runs", first.returncode == 0, first.stderr[-300:])
    out = (first.stdout + first.stderr).lower()
    step("ravix_cron reports its jobs", "publish" in out or "mail" in out or "cleanup" in out, out[-300:])
    backups = [p for p in (data / "backups").glob("*") if p.is_dir()]
    step("the daily backup was made inside the private data folder", bool(backups) and (backups[0] / "done").exists() and not inside(backups[0], home / "public_html"))
    second = manage(app, env, py, "ravix_cron", "--no-keep-alive")
    step("a second run right after does not repeat the daily backup", len([p for p in (data / "backups").glob("*") if p.is_dir()]) == len(backups), second.stderr[-200:])
    # overlapping runs: hold the lock in one process and start another
    holder = subprocess.Popen([py, "-c", "import time,fcntl,sys,os\nfrom pathlib import Path\np=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True)\nf=open(p,'a+');fcntl.flock(f,fcntl.LOCK_EX);print('locked',flush=True);time.sleep(8)", str(data / "run" / "cron.lock")],
                              stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    third = manage(app, env, py, "ravix_cron", "--no-keep-alive", check=False)
    holder.wait()
    step("an overlapping ravix_cron exits quietly without running", third.returncode == 0 and ("still active" in (third.stdout + third.stderr).lower() or "did nothing" in (third.stdout + third.stderr).lower()), third.stdout[-200:] + third.stderr[-200:])
    kept = manage(app, env, py, "ravix_cron", "--no-backup")
    step("keep-alive requests the home page", kept.returncode == 0)

    # -------- 7. page cache
    g = Browser(mount)
    st, h, html, _t = g.get(mount + "/", follow=4)
    page_url = mount + "/"
    # find the URL the guest finally lands on
    cur = mount + "/"
    for _i in range(4):
        st, h, html, _t = g.get(cur)
        if st in (301, 302) and "location" in h:
            cur = urlsplit(h["location"]).path
            continue
        break
    g.get(cur)
    st, h, html, t1 = g.get(cur)
    st2, h2, html2, t2 = g.get(cur)
    step("a guest's page is stored, then answered from the cache", h2.get("x-ravix-cache") == "HIT", (h.get("x-ravix-cache"), h2.get("x-ravix-cache")))
    best = min(g.get(cur)[3] for _i in range(5))
    step(f"a cached page answers in under 200 ms ({best*1000:.0f} ms)", best < 0.2)
    step("the cached page carries no cookie of its own", "set-cookie" not in h2)
    step("a signed-in user is never served from the public cache", b2.get(cur)[1].get("x-ravix-cache") != "HIT")
    step("a signed-in user's page differs from the guest cache", "owner" in b2.get(cur)[2].lower() or b2.get(cur)[1].get("x-ravix-cache") in ("BYPASS", None))
    cart = [p for p in (mount + "/shop/cart/", mount + "/cart/") if g.get(p)[0] == 200]
    for p in cart:
        step(f"{p} (cart) is never cached", g.get(p)[1].get("x-ravix-cache") != "HIT" and g.get(p)[1].get("x-ravix-cache") != "HIT")
    # editing content clears the cache
    manage(app, env, py, "shell", "-c", "from apps.core.models import SiteSettings as S; s=S.load(); s.site_name='Host Test 2'; s.save()")
    st3, h3, html3, _t = g.get(cur)
    step("editing the site clears the cache at once", h3.get("x-ravix-cache") == "MISS" and "Host Test 2" in html3, h3.get("x-ravix-cache"))
    print("\nALL HOSTING CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
