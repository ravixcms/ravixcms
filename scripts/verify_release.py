#!/usr/bin/env python3
"""راستی‌آزمایی بستهٔ انتشار Ravix پیش از آپلود (فقط‌خواندنی؛ هیچ‌چیز را تغییر نمی‌دهد).

استفاده:  python scripts/verify_release.py dist/ravix-X.Y.Z.zip [--version X.Y.Z]

بررسی می‌کند:
  ۱) پیشوند پوشهٔ ریشه «ravix-X.Y.Z/» و برابری X.Y.Z با RAVIX_VERSION درون خود بسته (و با --version اگر داده شود)؛
  ۲) وجود فایل‌های ضروری نصب/اجرا؛
  ۳) نبود فایل‌های ممنوع (آزمون‌ها، .github، پایگاه‌داده، .env، کلید، media، venv، کش‌ها و …) — بستهٔ کاربر ≠ بستهٔ توسعه؛
  ۴) نبود الگوهای اسرار (کلید خصوصی، توکن‌های شناخته‌شده) در فایل‌های متنی؛
  ۵) مطابقت SHA-256 با SHA256SUMS کنار فایل (اگر موجود باشد).
خروجی: فهرست PASS/FAIL، تعداد فایل‌ها، حجم و SHA-256؛ کد خروج ۰ فقط اگر همه PASS باشند.
"""
import hashlib
import re
import sys
import zipfile
from pathlib import Path

REQUIRED = [
    "manage.py", "requirements.txt", "requirements.lock", "LICENSE", "README.md", "CHANGELOG.md", "SECURITY.md",
    "THIRD_PARTY_NOTICES.md", ".env.example", "config/settings/base.py", "config/settings/production.py",
    "apps/core/version.py", "deploy/ravix.service", "deploy/nginx-ravix.conf", "deploy/gunicorn.conf.py",
    "deploy/backup.sh", "deploy/restore.sh", "docs/deployment.md", "docs/upgrade.md", "docs/themes.md",
    "themes/default/theme.json", "themes/default/templates/base.html", "static/css/ravix.css",
    "scripts/vps_smoke.sh", "install.sh", "Dockerfile", "docker-compose.yml", "docker/entrypoint.sh", "docs/installer.md",
]
# هر الگو روی مسیر نسبی (بدون پیشوند ریشه) آزموده می‌شود.
FORBIDDEN = [
    (r"(^|/)tests?(/|$)", "پوشهٔ آزمون"),
    (r"(^|/)test_[^/]*\.py$", "فایل آزمون"),
    (r"^\.github(/|$)", "زیرساخت CI"),
    (r"^\.gitattributes$", ".gitattributes"),
    (r"^scripts/(e2e_[^/]*|verify_release\.py|vps_theme_install_check\.py|bandit_check\.sh)$", "اسکریپت آزمایش/انتشار"),
    (r"^\.bandit-baseline\.json$", "baseline ابزار توسعه"),
    (r"^\.gitleaks\.toml$", "پیکربندی ابزار توسعه"),
    (r"(^|/)\.env$", "فایل .env"),
    (r"(^|/)db\.sqlite3$", "پایگاه‌دادهٔ SQLite"),
    (r"^media(/|$)", "پوشهٔ media"),
    (r"^staticfiles(/|$)", "staticfiles"),
    (r"(^|/)\.venv(/|$)", "محیط مجازی"),
    (r"(^|/)__pycache__(/|$)|\.pyc$", "کش پایتون"),
    (r"^keys(/|$)|\.key$|\.pem$", "کلید"),
    (r"^(plugins_installed|themes_installed|plugin_backups|shop_private|update_work|update_backups|registry_packages|dist)(/|$)", "داده/خروجی زمان اجرا"),
    (r"(^|/)\.git(/|$)", "پوشهٔ .git"),
]
SECRET_PATTERNS = [
    (re.compile(rb"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "کلید خصوصی"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "کلید AWS"),
    (re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"), "توکن GitHub"),
    (re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"), "توکن Slack"),
    (re.compile(rb"sk-[A-Za-z0-9]{32,}"), "کلید API"),
]
TEXT_EXT = {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".sh", ".conf", ".service", ".timer", ".html", ".css", ".js",
            ".example", ".lock", ".cfg", ".ini", ".toml", ""}


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0])
    want = argv[argv.index("--version") + 1] if "--version" in argv else None
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""))

    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    zf = zipfile.ZipFile(path)
    check("ZIP سالم است (CRC همهٔ فایل‌ها)", zf.testzip() is None)
    names = [i.filename for i in zf.infolist() if not i.is_dir()]
    m = re.fullmatch(r"ravix-(\d+\.\d+\.\d+[^/]*)\.zip", path.name)
    check("نام فایل به شکل ravix-X.Y.Z.zip است", bool(m), path.name)
    root = f"ravix-{m.group(1)}/" if m else None
    check("همهٔ فایل‌ها زیر یک پوشهٔ ریشهٔ ravix-X.Y.Z/ هستند", root and all(n.startswith(root) for n in names))
    rel = [n[len(root):] for n in names] if root else names

    ver_file = next((n for n in names if n.endswith("apps/core/version.py")), None)
    inner = re.search(r'RAVIX_VERSION = "([^"]+)"', zf.read(ver_file).decode()).group(1) if ver_file else None
    base = lambda v: v.split("-")[0]  # v0.9.0-rc1 ← RAVIX_VERSION = 0.9.0 (مثل مرحلهٔ تطبیق تگ در release.yml)
    check("RAVIX_VERSION درون بسته با شمارهٔ نسخهٔ نام فایل یکی است", m and inner == base(m.group(1)), f"{inner} ≠ {m.group(1) if m else '?'}")
    if want:
        check("نسخهٔ بسته با --version یکی است", inner == base(want), f"{inner} ≠ {want}")

    missing = [r for r in REQUIRED if r not in rel]
    check("همهٔ فایل‌های ضروری هستند", not missing, ", ".join(missing))
    bad = [(r, why) for r in rel for pat, why in FORBIDDEN if re.search(pat, r)]
    check("هیچ فایل ممنوعی در بسته نیست", not bad, "; ".join(f"{r} ({w})" for r, w in bad[:8]))

    hits = []
    for n, r in zip(names, rel):
        if Path(r).suffix.lower() in TEXT_EXT or Path(r).name in ("LICENSE", "Dockerfile"):
            blob = zf.read(n)
            for pat, why in SECRET_PATTERNS:
                if pat.search(blob):
                    hits.append(f"{r} ({why})")
    check("الگوی اسرار (کلید خصوصی/توکن) در فایل‌های متنی نیست", not hits, "; ".join(hits[:8]))
    check("هیچ فایلی بزرگ‌تر از 5MB نیست (بستهٔ کد، نه داده)", all(i.file_size < 5 * 1024 * 1024 for i in zf.infolist()))

    sums = path.with_name("SHA256SUMS")
    if sums.exists():
        line = next((l for l in sums.read_text().splitlines() if l.strip().endswith(path.name)), "")
        check("SHA-256 با SHA256SUMS یکی است", line.split()[:1] == [sha], line[:20])
    else:
        print("INFO  SHA256SUMS کنار فایل نیست؛ بررسی هش انجام نشد")

    print(f"INFO  فایل‌ها: {len(names)} | حجم: {len(data):,} بایت | SHA-256: {sha}")
    ok = all(results)
    print(f"نتیجه: {sum(results)}/{len(results)} PASS" + ("" if ok else " — ناموفق"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
