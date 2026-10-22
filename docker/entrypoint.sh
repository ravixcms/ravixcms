#!/usr/bin/env bash
# نقطهٔ ورود تصویر Ravix. نقش‌ها:  web (پیش‌فرض) | backup | manage <دستور> | هر دستور دیگر
set -euo pipefail

# کلید Django: اگر نه DJANGO_SECRET_KEY و نه فایل کلید هست، یکی می‌سازیم و در volume نگه می‌داریم تا با هر راه‌اندازی عوض نشود.
# (فایل به‌جز این‌جا از طریق RAVIX_SECRET_KEY_FILE در تنظیمات خوانده می‌شود، پس «docker compose exec ... manage.py» هم کار می‌کند.)
if [ -z "${DJANGO_SECRET_KEY:-}" ] && [ -n "${RAVIX_SECRET_KEY_FILE:-}" ] && [ ! -s "$RAVIX_SECRET_KEY_FILE" ]; then
  ( umask 077; python -c 'import secrets; print(secrets.token_urlsafe(64))' > "$RAVIX_SECRET_KEY_FILE" )
  echo "کلید Django ساخته و در $RAVIX_SECRET_KEY_FILE ذخیره شد (از آن پشتیبان جداگانه بگیرید)." >&2
fi

wait_for_db() {
  python - <<'PY'
import os, sys, time
import psycopg
url = os.environ.get("DATABASE_URL", "")
if not url.startswith(("postgres://", "postgresql://")):
    sys.exit("DATABASE_URL باید به PostgreSQL اشاره کند.")
for i in range(60):
    try:
        psycopg.connect(url, connect_timeout=3).close()
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        if i == 59:
            sys.exit(f"دیتابیس در دسترس نیست: {type(exc).__name__}")
        time.sleep(1)
PY
}

case "${1:-web}" in
  web)
    wait_for_db
    if [ "${RAVIX_AUTO_MIGRATE:-1}" = "1" ]; then python manage.py migrate --noinput; fi
    exec gunicorn config.wsgi:application -c deploy/gunicorn.conf.py
    ;;
  backup)
    wait_for_db
    exec /app/docker/backup-loop.sh
    ;;
  manage)
    shift
    exec python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
