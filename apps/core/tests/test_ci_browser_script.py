"""scripts/ci_install_browser.sh keeps the browser job from failing or stalling on a flaky apt mirror (the CI job once hung for 20 minutes)."""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "ci_install_browser.sh"

PLAYWRIGHT = """#!/usr/bin/env bash
# a stand-in: behaviour per command comes from files in its own folder: <cmd>.fail_first=N, <cmd>.always_fail, <cmd>.hang
d="$(dirname "$0")"; cmd="$1"
echo "$cmd" >> "$d/calls"
[ -e "$d/$cmd.hang" ] && sleep 30
[ -e "$d/$cmd.always_fail" ] && exit 1
if [ -e "$d/$cmd.fail_first" ]; then
  n=$(grep -c "^$cmd$" "$d/calls"); [ "$n" -le "$(cat "$d/$cmd.fail_first")" ] && exit 1
fi
exit 0
"""


class BrowserInstallScriptTests(SimpleTestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ravix-pw-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir / "playwright").write_text(PLAYWRIGHT)
        (self.dir / "python").write_text("#!/usr/bin/env bash\nexit 0\n")  # "Chromium starts"
        for name in ("playwright", "python"):
            (self.dir / name).chmod(0o755)

    def run_script(self, **env):
        t = time.monotonic()
        out = subprocess.run(["bash", str(SCRIPT), str(self.dir / "playwright")], capture_output=True, text=True, timeout=120,
                             env={**os.environ, "RAV_SUDO": "", "RAV_PAUSE": "0", "RAV_TRIES": "3", "RAV_TRY_TIMEOUT": "20", **env})
        return out, time.monotonic() - t

    def calls(self):
        p = self.dir / "calls"
        return p.read_text().split() if p.exists() else []

    def test_everything_works(self):
        out, _ = self.run_script()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.calls(), ["install-deps", "install"])
        self.assertIn("system libraries: ok", out.stdout)

    def test_apt_down_the_run_goes_on_without_the_system_libraries(self):
        (self.dir / "install-deps.always_fail").write_text("")
        out, _ = self.run_script()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.calls().count("install-deps"), 3)
        self.assertIn("system libraries: skipped", out.stdout)
        self.assertIn("WARNING", out.stdout)

    def test_a_flaky_download_is_tried_again(self):
        (self.dir / "install.fail_first").write_text("2")
        out, _ = self.run_script()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.calls().count("install"), 3)

    def test_a_dead_download_fails_the_job_with_that_reason(self):
        (self.dir / "install.always_fail").write_text("")
        out, _ = self.run_script()
        self.assertEqual(out.returncode, 1)
        self.assertIn("download failed", out.stderr)

    def test_a_hanging_apt_is_cut_off_by_the_timeout(self):
        (self.dir / "install-deps.hang").write_text("")
        out, elapsed = self.run_script(RAV_TRY_TIMEOUT="1", RAV_TRIES="2")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertLess(elapsed, 25)
        self.assertIn("skipped", out.stdout)

    def test_a_browser_that_does_not_start_is_the_reported_failure(self):
        (self.dir / "python").write_text("#!/usr/bin/env bash\nexit 1\n")
        out, _ = self.run_script()
        self.assertEqual(out.returncode, 1)
        self.assertIn("does not start", out.stderr)
