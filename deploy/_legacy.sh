# منبع‌شدنی. سازگاری با نصب‌های قدیمی (نام ravin) تا Ravix 2.0 — docs/upgrade.md
# 1) فایل تنظیمات: اگر /etc/ravix/ravix.env نیست ولی /etc/ravin/ravin.env هست، همان خوانده می‌شود.
# 2) هر RAVIN_X که RAVIX_X متناظر ندارد، به RAVIX_X کپی می‌شود (با هشدار روی stderr).
# 3) مسیرهای پیش‌فرض: اگر پوشهٔ ravix نیست ولی ravin هست، پوشهٔ قدیمی (rav_path).
rav_env_file() {
  if [ -n "${RAVIX_ENV_FILE:-}" ]; then echo "$RAVIX_ENV_FILE"
  elif [ ! -e /etc/ravix/ravix.env ] && [ -e /etc/ravin/ravin.env ]; then
    echo "هشدار: /etc/ravin/ravin.env منسوخ است؛ به /etc/ravix/ravix.env منتقل کنید (حذف در 2.0)" >&2
    echo /etc/ravin/ravin.env
  else echo /etc/ravix/ravix.env; fi
}
rav_legacy_vars() {
  local v new
  for v in $(compgen -e | grep '^RAVIN_' || true); do
    new="RAVIX_${v#RAVIN_}"
    if [ -z "${!new+x}" ]; then
      export "$new=${!v}"; echo "هشدار: متغیر $v منسوخ است؛ از $new استفاده کنید (حذف در 2.0)" >&2
    fi
  done
}
rav_path() {  # rav_path /srv/ravix/media  → همان، یا /srv/ravin/media اگر فقط قدیمی موجود است
  local new="$1" old="${1//ravix/ravin}"
  if [ ! -e "$new" ] && [ -e "$old" ]; then echo "$old"; else echo "$new"; fi
}
