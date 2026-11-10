"""اجرای مهاجرت‌های دیتابیس. در پروسهٔ جداگانه (``manage.py migrate``) تا کد تازهٔ روی دیسک بارگذاری شود نه کد قدیمیِ حافظه."""
from django.utils.translation import gettext
import os
import subprocess
import sys
from pathlib import Path

from .errors import UpdateError
from .logs import sanitize


class SubprocessRunner:
    def __init__(self, root: Path, timeout: int = 1800, python: Path | str | None = None):
        self.root, self.timeout = Path(root), timeout
        self.python = str(python) if python else sys.executable  # the new environment when the update built one

    def with_python(self, python: Path | str | None) -> "SubprocessRunner":
        return SubprocessRunner(self.root, self.timeout, python)

    def _manage(self, *args) -> str:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([self.python, str(self.root / "manage.py"), *args], cwd=self.root, env=env, capture_output=True, text=True, timeout=self.timeout)
        full = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0:
            # the reason is at the END of the output (the traceback); the start is only "Operations to perform: Apply all migrations…"
            text = sanitize(full, 20000)
            shown = text if len(text) <= 1500 else "…" + text[-1500:]
            raise UpdateError((gettext("manage.py %(args)s failed:\n%(out)s") % {"args": args[0], "out": shown}))
        return sanitize(full, 6000)

    def migrate(self) -> str:
        return self._manage("migrate", "--noinput")

    def migrate_back(self, targets) -> str:
        """هر هدف (app, name) را به آن مهاجرت برمی‌گرداند؛ ``zero`` یعنی ابتدای اپ."""
        out = []
        for app, name in targets:
            out.append(self._manage("migrate", app, name, "--noinput"))
        return "\n".join(out)

    def collectstatic(self) -> str:
        return self._manage("collectstatic", "--noinput", "-v", "0")

    def compile(self) -> None:
        """Byte-compile the new code once, so the first requests do not pay for it (the service user cannot write the code folder)."""
        subprocess.run([self.python, "-m", "compileall", "-q", "-j", "0", "apps", "config"], cwd=self.root, capture_output=True, timeout=self.timeout, check=False)  # noqa: S603  # nosec B603

    def check(self) -> str:
        return self._manage("check")
