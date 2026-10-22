#!/usr/bin/env bash
# بازیابی: restore.sh <ravin-STAMP.dump> [<STAMP>.media.tar.gz ...]    (هر آرشیو فایل به مسیر متناظرش بازیابی می‌شود)
# هشدار: پایگاه‌داده هدف را جایگزین می‌کند. ابتدا سرویس را متوقف کنید: sudo systemctl stop ravin
# قبل از هر کاری هش فایل‌ها با فایل .sha256 کنار پشتیبان سنجیده می‌شود. تأیید بدون پرسش: RAVIN_RESTORE_YES=1
set -euo pipefail
DUMP="${1:?مسیر فایل .dump را بدهید}"; shift || true
ENV_FILE="${RAVIN_ENV_FILE:-/etc/ravin/ravin.env}"
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
: "${DATABASE_URL:?DATABASE_URL تنظیم نشده}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMS="${DUMP%.dump}.sha256"
if [ -f "$SUMS" ]; then ( cd "$(dirname "$DUMP")" && sha256sum --check --quiet "$(basename "$SUMS")" ) || { echo "هش پشتیبان نمی‌خواند؛ بازیابی انجام نشد" >&2; exit 2; }
else echo "هشدار: فایل هش ($SUMS) نیست؛ سلامت فایل‌ها سنجیده نشد" >&2; fi
if [ "${RAVIN_RESTORE_YES:-}" != "1" ]; then
  read -r -p "پایگاه‌داده هدف با $DUMP جایگزین می‌شود. ادامه؟ (yes/no) " ans
  [ "$ans" = "yes" ] || { echo "لغو شد"; exit 1; }
fi
. "$HERE/_pgenv.sh"
pg_restore --clean --if-exists --no-owner --exit-on-error --single-transaction --dbname="$PGDATABASE" "$DUMP"
for TAR in "$@"; do
  case "$TAR" in
    *.media.tar.gz) DEST="$(dirname "${MEDIA_DIR:-/srv/ravin/media}")" ;;
    *.shop_private.tar.gz) DEST="$(dirname "${SHOP_PRIVATE_ROOT:-${RAVIN_SHOP_PRIVATE_ROOT:-/srv/ravin/app/shop_private}}")" ;;
    *.plugins_installed.tar.gz) DEST="$(dirname "${RAVIN_PLUGIN_INSTALL_DIR:-/srv/ravin/app/plugins_installed}")" ;;
    *.themes_installed.tar.gz) DEST="$(dirname "${RAVIN_THEME_INSTALL_DIR:-/srv/ravin/app/themes_installed}")" ;;
    *.plugin_backups.tar.gz) DEST="$(dirname "${RAVIN_PLUGIN_BACKUP_DIR:-/srv/ravin/app/plugin_backups}")" ;;
    *.registry.tar.gz) DEST="$(dirname "${RAVIN_REGISTRY_STORAGE:-/srv/ravin/app/registry_packages}")" ;;
    *) echo "آرشیو ناشناخته: $TAR" >&2; exit 3 ;;
  esac
  mkdir -p "$DEST"; tar -xzf "$TAR" -C "$DEST"
done
echo "بازیابی انجام شد. سپس: manage.py migrate && systemctl start ravin"
