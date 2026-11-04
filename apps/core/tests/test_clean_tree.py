from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from apps.core.management.commands import ravix_cron


class RunFilesStayOutOfTheProject(SimpleTestCase):
    def test_the_cron_lock_lives_in_the_temporary_data_folder_during_tests(self):
        lock = ravix_cron.lock_path().resolve()
        self.assertFalse(str(lock).startswith(str(Path(settings.BASE_DIR).resolve()) + "/"), lock)

    def test_the_run_folder_is_ignored_by_git(self):
        lines = (Path(settings.BASE_DIR) / ".gitignore").read_text().splitlines()
        self.assertIn("/run/", lines)
