#!/usr/bin/env bash
# بازیابی: restore.sh <ravix-STAMP.dump> [<STAMP>.media.tar.gz ...]    (هر آرشیو فایل به مسیر متناظرش بازیابی می‌شود)
# هشدار: پایگاه‌داده هدف را جایگزین می‌کند. ابتدا سرویس را متوقف کنید: sudo systemctl stop ravix
# قبل از هر کاری هش فایل‌ها با فایل .sha256 کنار پشتیبان سنجیده می‌شود. تأیید بدون پرسش: RAVIX_RESTORE_YES=1
set -euo pipefail
DUMP="${1:?مسیر فایل .dump را بدهید}"; shift || true
. "$(dirname "${BASH_SOURCE[0]}")/_legacy.sh"
ENV_FILE="$(rav_env_file)"
# shellcheck source=/dev/null
[ -r "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
rav_legacy_vars
: "${DATABASE_URL:?DATABASE_URL تنظیم نشده}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUMS="${DUMP%.dump}.sha256"
if [ -f "$SUMS" ]; then ( cd "$(dirname "$DUMP")" && sha256sum --check --quiet "$(basename "$SUMS")" ) || { echo "هش پشتیبان نمی‌خواند؛ بازیابی انجام نشد" >&2; exit 2; }
else echo "هشدار: فایل هش ($SUMS) نیست؛ سلامت فایل‌ها سنجیده نشد" >&2; fi
if [ "${RAVIX_RESTORE_YES:-}" != "1" ]; then
  read -r -p "پایگاه‌داده هدف با $DUMP جایگزین می‌شود. ادامه؟ (yes/no) " ans
  [ "$ans" = "yes" ] || { echo "لغو شد"; exit 1; }
fi
. "$HERE/_pgenv.sh"
pg_restore --clean --if-exists --no-owner --exit-on-error --single-transaction --dbname="$PGDATABASE" "$DUMP"
for TAR in "$@"; do
  case "$TAR" in
    *.media.tar.gz) DEST="$(dirname "${MEDIA_DIR:-$(rav_path /srv/ravix/media)}")" ;;
    *.shop_private.tar.gz) DEST="$(dirname "${SHOP_PRIVATE_ROOT:-${RAVIX_SHOP_PRIVATE_ROOT:-$(rav_path /srv/ravix/app/shop_private)}}")" ;;
    *.plugin_data.tar.gz) DEST="$(dirname "${RAVIX_PLUGIN_DATA_DIR:-$(rav_path /srv/ravix/app/plugin_data)}")" ;;
    *.plugins_installed.tar.gz) DEST="$(dirname "${RAVIX_PLUGIN_INSTALL_DIR:-$(rav_path /srv/ravix/app/plugins_installed)}")" ;;
    *.themes_installed.tar.gz) DEST="$(dirname "${RAVIX_THEME_INSTALL_DIR:-$(rav_path /srv/ravix/app/themes_installed)}")" ;;
    *.plugin_backups.tar.gz) DEST="$(dirname "${RAVIX_PLUGIN_BACKUP_DIR:-$(rav_path /srv/ravix/app/plugin_backups)}")" ;;
    *.registry.tar.gz) DEST="$(dirname "${RAVIX_REGISTRY_STORAGE:-$(rav_path /srv/ravix/app/registry_packages)}")" ;;
    *) echo "آرشیو ناشناخته: $TAR" >&2; exit 3 ;;
  esac
  mkdir -p "$DEST"; tar -xzf "$TAR" -C "$DEST"
done
echo "بازیابی انجام شد. سپس: manage.py migrate && systemctl start ravix"
