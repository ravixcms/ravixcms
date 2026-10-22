#!/usr/bin/env bash
# پشتیبان‌گیری از PostgreSQL + فایل‌های ضروری (media، فایل‌های دیجیتال فروشگاه، افزونه‌های نصب‌شده، مخزن). کد برنامه از مخزن کد بازیابی می‌شود.
# اجرا: sudo -u ravin /srv/ravin/app/deploy/backup.sh      تنظیمات از /etc/ravin/ravin.env خوانده می‌شود.
# خروجی: BACKUP_DIR/ravin-YYYYmmdd-HHMMSS.{dump,media.tar.gz,shop_private.tar.gz,...} + .sha256 (فهرست هش برای تأیید سلامت هنگام بازیابی)
# ⚠ فایل تنظیمات (/etc/ravin/ravin.env) و کلیدها (DJANGO_SECRET_KEY، RAVIN_SECRETS_KEY) عمداً در این پشتیبان نیستند؛ جداگانه و رمزنگاری‌شده نگه دارید.
set -euo pipefail
ENV_FILE="${RAVIN_ENV_FILE:-/etc/ravin/ravin.env}"
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
: "${DATABASE_URL:?DATABASE_URL تنظیم نشده}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/ravin}"
MEDIA_DIR="${MEDIA_DIR:-/srv/ravin/media}"
KEEP_COUNT="${KEEP_COUNT:-7}"   # تعداد پشتیبانِ کامل که نگه داشته می‌شود (۷ پشتیبان آخر)
case "$KEEP_COUNT" in ''|*[!0-9]*|0) echo "KEEP_COUNT باید عدد صحیح ≥ 1 باشد (مقدار: $KEEP_COUNT)" >&2; exit 1;; esac
umask 077
mkdir -p "$BACKUP_DIR"
. "$HERE/_pgenv.sh"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/ravin-$STAMP"
FILES=()
pg_dump --format=custom --no-owner --file="$OUT.dump"
pg_restore --list "$OUT.dump" >/dev/null          # فایل دامپ قابل‌خواندن است
FILES+=("$OUT.dump")
pack() {  # pack <پوشه> <پسوند>
  if [ -d "$1" ]; then tar -czf "$OUT.$2.tar.gz" -C "$(dirname "$1")" "$(basename "$1")"; tar -tzf "$OUT.$2.tar.gz" >/dev/null; FILES+=("$OUT.$2.tar.gz"); fi
}
pack "$MEDIA_DIR" media
pack "${SHOP_PRIVATE_ROOT:-${RAVIN_SHOP_PRIVATE_ROOT:-/srv/ravin/app/shop_private}}" shop_private
pack "${RAVIN_PLUGIN_INSTALL_DIR:-/srv/ravin/app/plugins_installed}" plugins_installed
pack "${RAVIN_PLUGIN_BACKUP_DIR:-/srv/ravin/app/plugin_backups}" plugin_backups
pack "${RAVIN_THEME_INSTALL_DIR:-/srv/ravin/app/themes_installed}" themes_installed   # قالب‌های نصب‌شده از پنل
pack "${RAVIN_REGISTRY_STORAGE:-/srv/ravin/app/registry_packages}" registry
( cd "$BACKUP_DIR" && sha256sum "${FILES[@]##*/}" > "ravin-$STAMP.sha256" )
# نگهداری: فقط پشتیبان‌های «کامل» (دارای .sha256) شمرده می‌شوند و KEEP_COUNT تای آخر می‌مانند؛ بقیه پاک می‌شوند.
# (اجرای ناقصِ شکست‌خورده جای پشتیبان سالم را نمی‌گیرد؛ بقایای ناقص بیش از ۱ روز قدیمی پاک می‌شوند.)
# حذف فقط پس از موفقیت این اجرا انجام می‌شود (بالا set -e است).
mapfile -t DONE < <(cd "$BACKUP_DIR" && ls -1 ravin-????????-??????.sha256 2>/dev/null | sed 's/\.sha256$//' | sort -r)
if [ "${#DONE[@]}" -gt "$KEEP_COUNT" ]; then
  for old in "${DONE[@]:$KEEP_COUNT}"; do rm -f -- "$BACKUP_DIR/$old".*; done
fi
for f in "$BACKUP_DIR"/ravin-????????-??????.*; do
  [ -e "$f" ] || continue
  b="$(basename "$f")"; stamp="${b%%.*}"
  [ -e "$BACKUP_DIR/$stamp.sha256" ] || find "$f" -maxdepth 0 -mtime +0 -delete   # ناقص و قدیمی‌تر از ۱ روز
done
echo "OK: $OUT.dump (+ $((${#FILES[@]} - 1)) آرشیو فایل)"
