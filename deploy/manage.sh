#!/usr/bin/env bash
# اجرای manage.py با تنظیمات ravix.env، بدون `source` کردن آن (مقدارهای بدون گیومه با < > یا فاصله هم کار می‌کنند).
# اجرا: sudo -u ravix /srv/ravix/app/deploy/manage.sh migrate        (مثلاً: ravix_preflight، check، createsuperuser)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_legacy.sh"
. "$HERE/_envfile.sh"
rav_load_env "$(rav_env_file)"
cd "$(dirname "$HERE")"
exec "${RAVIX_PYTHON:-$(rav_path /srv/ravix/venv)/bin/python}" manage.py "$@"
