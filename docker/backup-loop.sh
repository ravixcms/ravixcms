#!/usr/bin/env bash
# پشتیبان دوره‌ای داخل compose: deploy/backup.sh را هر BACKUP_INTERVAL_HOURS (پیش‌فرض ۲۴) ساعت اجرا می‌کند.
# خروجی در /backups (volume ravix_backups)، نگهداری ۷ پشتیبان آخر (KEEP_COUNT). شکست یک دور، حلقه را نمی‌شکند.
set -uo pipefail
hours="${BACKUP_INTERVAL_HOURS:-24}"
case "$hours" in ''|*[!0-9]*|0) echo "BACKUP_INTERVAL_HOURS باید عدد صحیح ≥ 1 باشد" >&2; exit 1;; esac
sleep "${BACKUP_FIRST_DELAY_SECONDS:-120}"   # فرصت برای مهاجرت‌های اولیهٔ برنامه
while true; do
  if /app/deploy/backup.sh; then date -u +%FT%TZ > /backups/.last_ok; else echo "پشتیبان‌گیری ناموفق بود؛ دور بعد دوباره تلاش می‌شود" >&2; fi
  sleep $(( hours * 3600 ))
done
