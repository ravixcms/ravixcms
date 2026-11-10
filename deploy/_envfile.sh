# shellcheck shell=bash
# منبع‌شدنی. خواندن امنِ فایل تنظیمات (ravix.env) بدون اجرای آن به‌عنوان کد شل.
# چرا: `set -a; . ravix.env` اگر مقداری مثل  DEFAULT_FROM_EMAIL=Ravix <a@b.c>  بدون گیومه باشد، با «syntax error» می‌شکند (systemd و python-dotenv
# همین خط را درست می‌خوانند). این تابع هر خط KEY=VALUE را «تحت‌اللفظی» می‌خواند، همان‌طور که systemd می‌خواند: یک جفت گیومهٔ دوطرفه برداشته می‌شود،
# \" و \\ داخل گیومهٔ دوتایی باز می‌شوند، و هیچ‌چیز (فاصله، < > & ; $ ` ( )) تفسیر نمی‌شود.
rav_load_env() {  # rav_load_env FILE → KEY=VALUEها را export می‌کند (فایل خوانا نباشد، کاری نمی‌کند)
  local f="${1:-}" line k v rx_line='^(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$' rx_dq='^"(.*)"$' rx_sq="^'(.*)'\$"
  [ -r "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in ''|'#'*) continue;; esac
    [[ "$line" =~ $rx_line ]] || continue
    k="${BASH_REMATCH[2]}"; v="${BASH_REMATCH[3]}"
    v="${v#"${v%%[![:space:]]*}"}"; v="${v%"${v##*[![:space:]]}"}"
    if [[ "$v" =~ $rx_dq ]]; then v="${BASH_REMATCH[1]}"; v="${v//\\\\/$'\001'}"; v="${v//\\\"/\"}"; v="${v//$'\001'/\\}"
    elif [[ "$v" =~ $rx_sq ]]; then v="${BASH_REMATCH[1]}"; fi
    export "$k=$v"
  done < "$f"
}
