"""اجرای مهاجرت‌های دیتابیس. در پروسهٔ جداگانه (``manage.py migrate``) تا کد تازهٔ روی دیسک بارگذاری شود نه کد قدیمیِ حافظه."""
import os
import subprocess
import sys
from pathlib import Path

from .errors import UpdateError
from .logs import sanitize


class SubprocessRunner:
    def __init__(self, root: Path, timeout: int = 1800):
        self.root, self.timeout = Path(root), timeout

    def _manage(self, *args) -> str:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, str(self.root / "manage.py"), *args], cwd=self.root, env=env, capture_output=True, text=True, timeout=self.timeout)
        out = sanitize((r.stdout or "") + (r.stderr or ""), 6000)
        if r.returncode != 0:
            raise UpdateError(f"manage.py {args[0]} ناموفق بود:\n{out}")
        return out

    def migrate(self) -> str:
        return self._manage("migrate", "--noinput")

    def migrate_back(self, targets) -> str:
        """هر هدف (app, name) را به آن مهاجرت برمی‌گرداند؛ ``zero`` یعنی ابتدای اپ."""
        out = []
        for app, name in targets:
            out.append(self._manage("migrate", app, name, "--noinput"))
        return "\n".join(out)

    def check(self) -> str:
        return self._manage("check")
