#!/usr/bin/env bash
# نصب Ravix روی Ubuntu 22.04 / 24.04:  Python، PostgreSQL، کاربر سیستمی، Gunicorn+systemd، Nginx و Let's Encrypt (certbot).
#
#   sudo ./install.sh                                   # تعاملی؛ پیش از هر تغییر می‌پرسد
#   sudo ./install.sh --domain example.com --email me@example.com --yes   # غیرتعاملی
#   sudo ./install.sh --dry-run                         # فقط نشان می‌دهد چه می‌کرد
#
# Idempotent: هر مرحله ابتدا وضعیت را می‌سنجد و اگر انجام شده باشد رد می‌شود؛ اجرای دوباره امن است.
# رازها (رمز دیتابیس، کلید Django، توکن نصب) فقط در /etc/ravix/ravix.env (مجوز 640) نوشته و هرگز بازنویسی نمی‌شوند.
# راهنما: docs/installer.md
set -Eeuo pipefail
IFS=$'\n\t'

# ----------------------------------------------------------------- پیش‌فرض‌ها و گزینه‌ها
APP_USER=ravix
BASE=/srv/ravix
APP_DIR=$BASE/app
VENV=$BASE/venv
ENV_DIR=/etc/ravix
ENV_FILE=$ENV_DIR/ravix.env
BACKUP_DIR=/var/backups/ravix
DB_NAME=ravix
DB_USER=ravix
LOG_FILE=/var/log/ravix-install.log
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DOMAIN=""
LE_EMAIL=""
WWW=1
ASSUME_YES=0
DRY_RUN=0
SSL=1
NGINX=1
SYSTEMD=auto
CHANGES=0

usage() {
  cat <<'EOF'
استفاده: sudo ./install.sh [گزینه‌ها]
  --domain D       دامنهٔ سایت (بدون http://)
  --email E        ایمیل برای Let's Encrypt (اطلاع‌رسانی انقضا)
  --no-www         www.D را در nginx/گواهی نیاور
  --no-ssl         بدون certbot (فقط وقتی TLS جای دیگری تمام می‌شود: Cloudflare، لودبالانسر)
  --no-nginx       nginx را دست نزن (پراکسی را خودتان می‌سازید)
  --no-systemd     سرویس/تایمر systemd نساز (مثلاً کانتینر)
  --source DIR     مسیر کد (پیش‌فرض: پوشهٔ همین اسکریپت)
  -y, --yes        به همهٔ پرسش‌ها «بله» (غیرتعاملی)
  -n, --dry-run    هیچ تغییری نده؛ فقط چاپ کن
  -h, --help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:?}"; shift 2;;
    --email) LE_EMAIL="${2:?}"; shift 2;;
    --no-www) WWW=0; shift;;
    --no-ssl) SSL=0; shift;;
    --no-nginx) NGINX=0; SSL=0; shift;;
    --no-systemd) SYSTEMD=no; shift;;
    --source) SRC="$(cd "${2:?}" && pwd)"; shift 2;;
    -y|--yes) ASSUME_YES=1; shift;;
    -n|--dry-run) DRY_RUN=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "گزینهٔ ناشناخته: $1" >&2; usage >&2; exit 2;;
  esac
done

# ----------------------------------------------------------------- ابزارهای کمکی
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✔%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%s✘ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
trap 'die "خطای پیش‌بینی‌نشده در خط $LINENO (دستور: $BASH_COMMAND). لاگ: $LOG_FILE"' ERR

run() {  # اجرای دستور تغییردهنده؛ در dry-run فقط چاپ می‌شود
  if [ "$DRY_RUN" = 1 ]; then (IFS=' '; printf '  [dry-run] %s\n' "$*"); return 0; fi
  CHANGES=$((CHANGES + 1)); "$@"
}

confirm() {  # confirm "پرسش" → 0=بله
  if [ "$ASSUME_YES" = 1 ] || [ "$DRY_RUN" = 1 ]; then printf '  ? %s  [بله]\n' "$1"; return 0; fi
  [ -t 0 ] || die "ورودی تعاملی نیست؛ با --yes اجرا کنید یا از ترمینال اجرا کنید."
  local a; read -r -p "  ? $1 [y/N] " a; [[ "$a" =~ ^([yY]|yes|بله)$ ]]
}

ask() {  # ask VAR "پرسش" [پیش‌فرض]
  local __var="$1" __q="$2" __def="${3:-}" __a=""
  if [ -n "${!__var}" ]; then return 0; fi
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then printf -v "$__var" '%s' "$__def"; return 0; fi
  read -r -p "  ? $__q${__def:+ [$__def]} " __a; printf -v "$__var" '%s' "${__a:-$__def}"
}

rand_hex() { openssl rand -hex "$1"; }
as_app() { runuser -u "$APP_USER" -- "$@"; }
envsh() {  # اجرای دستور به‌عنوان کاربر سرویس با متغیرهای ravix.env
  runuser -u "$APP_USER" -- bash -c 'set -a; . "$1"; set +a; shift; cd '"$APP_DIR"'; exec "$@"' _ "$ENV_FILE" "$@"
}
env_get() { [ -r "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
env_set() {  # env_set KEY VALUE — فقط اگر کلید نبود می‌افزاید؛ هرگز مقدار موجود را عوض نمی‌کند
  if [ -n "$(env_get "$1")" ]; then return 0; fi
  [ "$DRY_RUN" = 1 ] && { printf '  [dry-run] %s ← (تنظیم می‌شد)\n' "$1"; return 0; }
  printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; CHANGES=$((CHANGES + 1))
}
have_systemd() {
  case "$SYSTEMD" in no) return 1;; esac
  [ -d /run/systemd/system ]
}
pkg_missing() { local p; for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done; }

# ----------------------------------------------------------------- ۰. بررسی‌های اولیه
step "بررسی پیش‌نیازها"
[ "$(id -u)" = 0 ] || die "با دسترسی root اجرا کنید: sudo ./install.sh"
if [ "$DRY_RUN" = 0 ]; then mkdir -p "$(dirname "$LOG_FILE")"; exec > >(tee -a "$LOG_FILE") 2>&1; fi
. /etc/os-release 2>/dev/null || true
if [ "${ID:-}" != "ubuntu" ] || { [ "${VERSION_ID:-}" != "22.04" ] && [ "${VERSION_ID:-}" != "24.04" ]; }; then
  if [ "${RAVIX_SKIP_OS_CHECK:-0}" = 1 ]; then warn "سیستم ${PRETTY_NAME:-نامعلوم} پشتیبانی‌نشده است؛ به‌دلیل RAVIX_SKIP_OS_CHECK=1 ادامه می‌دهیم"
  else die "فقط Ubuntu 22.04 و 24.04 پشتیبانی می‌شود (این سیستم: ${PRETTY_NAME:-نامعلوم})."; fi
fi
ok "سیستم‌عامل: ${PRETTY_NAME:-?}"
[ -f "$SRC/manage.py" ] && [ -f "$SRC/requirements.lock" ] || die "کد Ravix در $SRC پیدا نشد (manage.py / requirements.lock). --source را بدهید."
ok "کد: $SRC"
if have_systemd; then ok "systemd فعال است"; else warn "systemd در حال اجرا نیست؛ سرویس/تایمر ساخته نمی‌شود (برای کانتینر طبیعی است)"; SYSTEMD=no; fi

if [ "$NGINX" = 1 ] || [ "$SSL" = 1 ]; then
  ask DOMAIN "دامنهٔ سایت (مثل example.com):"
  [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "دامنه نامعتبر یا خالی است (--domain)."
else
  ask DOMAIN "نام میزبان/دامنه برای ALLOWED_HOSTS:" "localhost"
fi
if [ "$SSL" = 1 ]; then ask LE_EMAIL "ایمیل برای Let's Encrypt (خالی = بدون ایمیل):"; fi
[ "$SSL" = 0 ] && [ "$NGINX" = 1 ] && warn "بدون TLS: کوکی‌های نشست در production «Secure» هستند؛ ورود فقط پشت HTTPS (پایان‌دهندهٔ TLS دیگر) کار می‌کند."

cat <<EOF

  این اسکریپت (با تأیید شما در هر مرحله) این کارها را انجام می‌دهد:
    • نصب بسته‌های apt ناقص (python3-venv، postgresql، nginx$([ "$SSL" = 1 ] && echo '، certbot')، rsync، openssl)
    • کاربر سیستمی «$APP_USER» و پوشه‌ها در $BASE، تنظیمات در $ENV_DIR
    • کپی کد به $APP_DIR و ساخت virtualenv در $VENV
    • ساخت نقش/دیتابیس PostgreSQL «$DB_NAME» با رمز تصادفی
    • migrate، collectstatic، سرویس Gunicorn و تایمرهای systemd
    • سایت nginx برای «$DOMAIN»$([ "$SSL" = 1 ] && echo " و گواهی Let's Encrypt")
EOF
confirm "ادامه می‌دهید؟" || { echo "لغو شد؛ هیچ تغییری داده نشد."; exit 0; }

# ----------------------------------------------------------------- ۱. بسته‌های apt
step "بسته‌های سیستمی"
PKGS=(python3 python3-venv ca-certificates curl openssl rsync postgresql postgresql-client)
[ "$NGINX" = 1 ] && PKGS+=(nginx)
[ "$SSL" = 1 ] && PKGS+=(certbot python3-certbot-nginx)
mapfile -t MISSING < <(pkg_missing "${PKGS[@]}")
if [ "${#MISSING[@]}" -gt 0 ] && [ -n "${MISSING[0]}" ]; then
  echo "  بسته‌های ناقص: $(IFS=' '; echo "${MISSING[*]}")"
  confirm "نصب با apt؟" || die "بدون این بسته‌ها نمی‌توان ادامه داد."
  run apt-get update -y
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${MISSING[@]}"
else
  ok "همهٔ بسته‌های لازم نصب‌اند"
fi

# پایتون ≥ 3.11 (Ubuntu 22.04 فقط 3.10 دارد ⇒ python3.12 از deadsnakes با تأیید شما)
PYBIN=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PYBIN="$(command -v "$c")"; break; fi
done
if [ -z "$PYBIN" ]; then
  warn "پایتون ۳٫۱۱ یا بالاتر پیدا نشد (Ubuntu ${VERSION_ID:-?} نسخهٔ قدیمی‌تر دارد)."
  confirm "افزودن مخزن deadsnakes و نصب python3.12؟" || die "پایتون ≥ 3.11 لازم است."
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends software-properties-common
  run add-apt-repository -y ppa:deadsnakes/ppa
  run apt-get update -y
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3.12 python3.12-venv
  PYBIN=/usr/bin/python3.12
fi
ok "پایتون: $PYBIN ($("$PYBIN" -V 2>&1 || echo '?'))"

# ----------------------------------------------------------------- ۲. کاربر و پوشه‌ها
step "کاربر سیستمی و پوشه‌ها"
if id "$APP_USER" >/dev/null 2>&1; then ok "کاربر $APP_USER وجود دارد"
else
  confirm "ساخت کاربر سیستمی «$APP_USER» (بدون ورود، home=$BASE)؟" || die "کاربر لازم است."
  run adduser --system --group --home "$BASE" --shell /usr/sbin/nologin "$APP_USER"
fi
if getent group "$APP_USER" | grep -qw www-data; then ok "www-data عضو گروه $APP_USER است"
else
  [ "$NGINX" = 1 ] && { confirm "افزودن www-data به گروه $APP_USER (برای خواندن سوکت/media)؟" && run usermod -aG "$APP_USER" www-data; }
fi
CACHE_DIR=/var/cache/ravix   # کش فایلی مشترک بین ورکرها (systemd هم با CacheDirectory= می‌سازد)
DIRS=(app media shop_private plugins_installed plugin_backups themes_installed registry_packages update_work update_backups)
need_dirs=0; for d in "${DIRS[@]}"; do [ -d "$BASE/$d" ] || need_dirs=1; done
[ -d "$ENV_DIR" ] && [ -d "$BACKUP_DIR" ] && [ -d "$CACHE_DIR" ] || need_dirs=1
if [ "$need_dirs" = 1 ]; then
  confirm "ساخت پوشه‌ها زیر $BASE، $ENV_DIR و $BACKUP_DIR؟" || die "پوشه‌ها لازم‌اند."
  for d in "${DIRS[@]}"; do run mkdir -p "$BASE/$d"; done
  run mkdir -p "$ENV_DIR" "$BACKUP_DIR" "$CACHE_DIR"
else ok "پوشه‌ها موجودند"
fi
if [ "$DRY_RUN" = 0 ]; then
  chown "$APP_USER:$APP_USER" "$BASE"; chmod 750 "$BASE"
  chown "$APP_USER:www-data" "$BASE/media"; chmod 2750 "$BASE/media"
  for d in shop_private plugins_installed themes_installed registry_packages update_work update_backups; do chown "$APP_USER:$APP_USER" "$BASE/$d"; chmod 750 "$BASE/$d"; done
  chown "$APP_USER:$APP_USER" "$BASE/plugin_backups"; chmod 700 "$BASE/plugin_backups"
  chown "$APP_USER:$APP_USER" "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  chown "$APP_USER:www-data" "$CACHE_DIR"; chmod 750 "$CACHE_DIR"
  chown root:"$APP_USER" "$ENV_DIR"; chmod 750 "$ENV_DIR"
fi

# ----------------------------------------------------------------- ۳. کد
step "کد برنامه"
if [ "$SRC" = "$APP_DIR" ]; then ok "کد از پیش در $APP_DIR است"
else
  if [ -f "$APP_DIR/manage.py" ]; then
    echo "  نصب قبلی پیدا شد؛ فقط فایل‌های کد روی آن همگام می‌شوند (دادهٔ سایت، media و تنظیمات دست‌نخورده)."
    q="به‌روزرسانی کد در $APP_DIR (rsync بدون حذف)؟"
  else
    q="کپی کد از $SRC به $APP_DIR؟"
  fi
  if confirm "$q"; then
    run rsync -a --exclude='.git' --exclude='.github' --exclude='.venv' --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='db.sqlite3' --exclude='*.sqlite3' --exclude='.env' --exclude='.env.*' --exclude='/media' --exclude='/staticfiles' \
      --exclude='/shop_private' --exclude='/plugins_installed' --exclude='/plugin_backups' --exclude='/themes_installed' \
      --exclude='/registry_packages' --exclude='/update_work' --exclude='/update_backups' --exclude='/install_token' \
      --exclude='/dist' --exclude='/keys' --exclude='*.key' --exclude='/certs' "$SRC"/ "$APP_DIR"/
  elif [ ! -f "$APP_DIR/manage.py" ]; then die "کد لازم است."
  else warn "کد به‌روز نشد"; fi
fi
if [ "$DRY_RUN" = 0 ] && [ -d "$APP_DIR" ]; then
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
  # افزونه‌های همراه کد: فقط‌خواندنی برای کاربر سرویس (مثل docs/deployment.md)
  [ -d "$APP_DIR/plugins" ] && { chown -R root:"$APP_USER" "$APP_DIR/plugins"; chmod -R g-w,o-rwx "$APP_DIR/plugins"; }
  [ -e "$APP_DIR/media" ] || ln -s "$BASE/media" "$APP_DIR/media"   # MEDIA_ROOT = BASE_DIR/media
  chown -h "$APP_USER:$APP_USER" "$APP_DIR/media" 2>/dev/null || true
fi

CODE="$APP_DIR"; [ -d "$APP_DIR/deploy" ] || CODE="$SRC"   # در dry-run کد هنوز کپی نشده است

# ----------------------------------------------------------------- ۴. virtualenv و وابستگی‌ها
step "virtualenv و وابستگی‌ها"
LOCK_SUM="$(sha256sum "$SRC/requirements.lock" | cut -c1-64)"
if [ -x "$VENV/bin/python" ]; then ok "virtualenv موجود است"
else
  confirm "ساخت virtualenv در $VENV؟" || die "virtualenv لازم است."
  run as_app "$PYBIN" -m venv "$VENV"
fi
if [ -f "$VENV/.requirements.sha256" ] && [ "$(cat "$VENV/.requirements.sha256")" = "$LOCK_SUM" ]; then ok "وابستگی‌ها با requirements.lock هم‌خوان‌اند"
else
  confirm "نصب وابستگی‌ها از requirements.lock با pip؟" || die "وابستگی‌ها لازم‌اند."
  run as_app "$VENV/bin/pip" install --disable-pip-version-check -r "$APP_DIR/requirements.lock"
  [ "$DRY_RUN" = 1 ] || echo "$LOCK_SUM" | as_app tee "$VENV/.requirements.sha256" >/dev/null
fi

# ----------------------------------------------------------------- ۵. PostgreSQL
step "PostgreSQL"
if have_systemd; then run systemctl enable --now postgresql
elif ! pg_isready -q 2>/dev/null; then
  warn "PostgreSQL در حال اجرا نیست و systemd نداریم؛ آن را خودتان بالا بیاورید (مثلاً pg_ctlcluster)."
fi
psql_admin() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 -X -q "$@"; }
if [ "$DRY_RUN" = 1 ] && ! pg_isready -q 2>/dev/null; then
  warn "[dry-run] PostgreSQL فعلاً بالا نیست؛ ساخت نقش/دیتابیس چاپ می‌شود"; ROLE_EXISTS=""; DB_EXISTS=""
else
  pg_isready -q || die "PostgreSQL پاسخ نمی‌دهد."
  ROLE_EXISTS="$(psql_admin -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")"
  DB_EXISTS="$(psql_admin -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")"
fi
DB_PASS=""
if [ -n "$(env_get DATABASE_URL)" ]; then ok "DATABASE_URL از قبل در $ENV_FILE هست"
else
  DB_PASS="$(rand_hex 24)"
  if [ -z "$ROLE_EXISTS" ]; then
    confirm "ساخت نقش PostgreSQL «$DB_USER» با رمز تصادفی؟" || die "نقش دیتابیس لازم است."
    [ "$DRY_RUN" = 1 ] || printf "CREATE ROLE %s LOGIN PASSWORD :'pw';\n" "$DB_USER" | psql_admin -v pw="$DB_PASS"
  else
    warn "نقش «$DB_USER» هست ولی رمزش در $ENV_FILE نیست."
    confirm "رمز نقش را با رمز تصادفی تازه عوض کنم؟ (برنامه‌های دیگری که از این نقش استفاده می‌کنند خراب می‌شوند)" || die "بدون رمز نقش نمی‌توان ادامه داد."
    [ "$DRY_RUN" = 1 ] || printf "ALTER ROLE %s LOGIN PASSWORD :'pw';\n" "$DB_USER" | psql_admin -v pw="$DB_PASS"
  fi
fi
if [ -z "$DB_EXISTS" ]; then
  confirm "ساخت دیتابیس «$DB_NAME» (مالک $DB_USER، UTF8)؟" || die "دیتابیس لازم است."
  [ "$DRY_RUN" = 1 ] || psql_admin -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8' TEMPLATE template0"
else ok "دیتابیس $DB_NAME موجود است"; fi

# ----------------------------------------------------------------- ۶. تنظیمات محیط
step "تنظیمات ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
  confirm "ساخت $ENV_FILE (مجوز 640، رمزهای تصادفی)؟" || die "فایل تنظیمات لازم است."
  [ "$DRY_RUN" = 1 ] || { install -m 640 -o root -g "$APP_USER" /dev/null "$ENV_FILE"; printf '# ساخته‌شده توسط install.sh — رازها را در مخزن کد نگذارید\n' >> "$ENV_FILE"; }
fi
ALLOWED="$DOMAIN"; ORIGINS="https://$DOMAIN"
if [ "$WWW" = 1 ] && [ "$NGINX" = 1 ] && [[ "$DOMAIN" == *.* ]]; then ALLOWED="$DOMAIN,www.$DOMAIN"; ORIGINS="https://$DOMAIN,https://www.$DOMAIN"; fi
env_set DJANGO_SETTINGS_MODULE config.settings.production
env_set DJANGO_SECRET_KEY "$("$PYBIN" -c 'import secrets; print(secrets.token_urlsafe(64))')"
env_set DJANGO_ALLOWED_HOSTS "$ALLOWED"
env_set DJANGO_CSRF_TRUSTED_ORIGINS "$ORIGINS"
[ -n "$DB_PASS" ] && env_set DATABASE_URL "postgres://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME"
env_set RAVIX_SITE_URL "https://$DOMAIN"
env_set RAVIX_INSTALL_TOKEN "$(rand_hex 16)"
env_set RAVIX_SHOP_PRIVATE_ROOT "$BASE/shop_private"
env_set RAVIX_PLUGIN_INSTALL_DIR "$BASE/plugins_installed"
env_set RAVIX_PLUGIN_BACKUP_DIR "$BASE/plugin_backups"
env_set RAVIX_THEME_INSTALL_DIR "$BASE/themes_installed"
env_set RAVIX_REGISTRY_STORAGE "$BASE/registry_packages"
env_set RAVIX_UPDATE_WORK_DIR "$BASE/update_work"
env_set RAVIX_TRUST_PROXY_HEADERS "$([ "$NGINX" = 1 ] && echo true || echo false)"
[ "$SSL" = 0 ] && env_set DJANGO_SECURE_SSL_REDIRECT false
[ "$DRY_RUN" = 1 ] || { chown root:"$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"; }
ok "تنظیمات آماده است (کلیدهای موجود دست نخوردند)"

# ----------------------------------------------------------------- ۷. migrate / static
step "migrate و فایل‌های استاتیک"
if [ "$DRY_RUN" = 1 ]; then
  echo "  [dry-run] manage.py migrate --noinput && collectstatic --noinput"
else
  if confirm "اجرای migrate و collectstatic؟"; then
    envsh "$VENV/bin/python" manage.py migrate --noinput
    envsh "$VENV/bin/python" manage.py collectstatic --noinput >/dev/null
    ok "migrate و collectstatic انجام شد"
  fi
fi

# ----------------------------------------------------------------- ۸. systemd
step "سرویس‌ها (systemd)"
if have_systemd; then
  UNITS=(ravix.service ravix-publish-scheduled.service ravix-publish-scheduled.timer ravix-backup.service ravix-backup.timer ravix-expire-orders.service ravix-expire-orders.timer)
  changed=0
  for u in "${UNITS[@]}"; do cmp -s "$CODE/deploy/$u" "/etc/systemd/system/$u" 2>/dev/null || changed=1; done
  if [ "$changed" = 1 ]; then
    confirm "نصب فایل‌های unit در /etc/systemd/system؟" || die "سرویس لازم است."
    for u in "${UNITS[@]}"; do run install -m 644 "$CODE/deploy/$u" "/etc/systemd/system/$u"; done
    run systemctl daemon-reload
  else ok "unitها به‌روزند"; fi
  confirm "فعال‌سازی و شروع ravix.service و تایمرها؟" && {
    run systemctl enable --now ravix.service ravix-publish-scheduled.timer ravix-backup.timer
    grep -qi '^RAVIX_SHOP_ENABLED=false' "$ENV_FILE" || run systemctl enable --now ravix-expire-orders.timer
    run systemctl restart ravix.service
  }
else
  warn "رد شد (بدون systemd). اجرای دستی: cd $APP_DIR && gunicorn config.wsgi:application -c deploy/gunicorn.conf.py"
fi

# ----------------------------------------------------------------- ۹. nginx
if [ "$NGINX" = 1 ]; then
  step "Nginx"
  SITE=/etc/nginx/sites-available/ravix
  SERVER_NAMES="$DOMAIN"; [ "$WWW" = 1 ] && [[ "$DOMAIN" == *.* ]] && SERVER_NAMES="$DOMAIN www.$DOMAIN"
  TMP_SITE="$(mktemp)"
  sed "s/server_name example.com www.example.com;/server_name $SERVER_NAMES;/" "$CODE/deploy/nginx-ravix.conf" > "$TMP_SITE"
  PROXY_SRC="$CODE/deploy/ravix-proxy.conf"
  # هستهٔ بدون IPv6 (دیده شده در کانتینرها/VPSهای خاص): listen [::] باعث شکست nginx -t می‌شود
  [ -e /proc/net/if_inet6 ] || { sed -i '/listen \[::\]/d' "$TMP_SITE"; warn "IPv6 در این سیستم فعال نیست؛ خطوط listen [::] حذف شد"; }
  if [ "$SSL" = 0 ]; then  # TLS جای دیگری تمام می‌شود: X-Forwarded-Proto بالادستی را بپذیر
    { printf 'map $http_x_forwarded_proto $ravix_proto { default $scheme; https https; }\n'; cat "$TMP_SITE"; } > "$TMP_SITE.2" && mv "$TMP_SITE.2" "$TMP_SITE"
    PROXY_SRC="$(mktemp)"; sed 's/\$scheme/$ravix_proto/' "$CODE/deploy/ravix-proxy.conf" > "$PROXY_SRC"
  fi
  if cmp -s "$TMP_SITE" "$SITE" 2>/dev/null && cmp -s "$PROXY_SRC" /etc/nginx/ravix-proxy.conf 2>/dev/null; then ok "پیکربندی nginx به‌روز است"
  else
    if [ -f "$SITE" ] && grep -q "managed by Certbot" "$SITE" 2>/dev/null; then
      warn "$SITE را certbot تغییر داده؛ بازنویسی نمی‌کنم (برای تغییر دستی ویرایش کنید)."
    else
      confirm "نصب پیکربندی nginx برای $SERVER_NAMES (در صورت وجود، فایل قبلی با پسوند .bak ذخیره می‌شود)؟" && {
        [ -f "$SITE" ] && run cp -a "$SITE" "$SITE.bak.$(date +%s)"
        run install -m 644 "$PROXY_SRC" /etc/nginx/ravix-proxy.conf
        run install -m 644 "$TMP_SITE" "$SITE"
        run ln -sfn "$SITE" /etc/nginx/sites-enabled/ravix
        if [ -e /etc/nginx/sites-enabled/default ]; then confirm "حذف سایت پیش‌فرض nginx (sites-enabled/default)؟" && run rm -f /etc/nginx/sites-enabled/default; fi
      }
    fi
  fi
  rm -f "$TMP_SITE"
  if [ "$DRY_RUN" = 0 ]; then
    nginx -t
    if have_systemd; then systemctl enable --now nginx && systemctl reload nginx; else warn "nginx را خودتان reload کنید"; fi
  fi
fi

# ----------------------------------------------------------------- ۱۰. Let's Encrypt
if [ "$SSL" = 1 ]; then
  step "گواهی Let's Encrypt"
  if [ -e "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then ok "گواهی $DOMAIN از قبل هست (certbot تمدید را خودکار انجام می‌دهد)"
  else
    resolved="$(getent ahostsv4 "$DOMAIN" | awk '{print $1; exit}' || true)"
    mine=" $(hostname -I 2>/dev/null) "
    if [ -z "$resolved" ]; then warn "DNS دامنهٔ $DOMAIN هنوز resolve نمی‌شود؛ certbot شکست می‌خورد."
    elif [[ "$mine" != *" $resolved "* ]]; then warn "DNS به $resolved اشاره دارد که IP محلی این سرور نیست (پشت NAT/CDN ممکن است طبیعی باشد)."
    else ok "DNS به این سرور اشاره دارد ($resolved)"; fi
    CB=(certbot --nginx --non-interactive --agree-tos --no-eff-email --redirect -d "$DOMAIN")
    [ "$WWW" = 1 ] && [[ "$DOMAIN" == *.* ]] && CB+=(-d "www.$DOMAIN")
    if [ -n "$LE_EMAIL" ]; then CB+=(-m "$LE_EMAIL"); else CB+=(--register-unsafely-without-email); fi
    echo "  دستور: $(IFS=' '; echo "${CB[*]}")"
    if confirm "گرفتن گواهی با certbot؟ (پورت ۸۰ باید از اینترنت باز و DNS درست باشد)"; then
      run "${CB[@]}" || warn "certbot ناموفق بود؛ بعد از درست‌شدن DNS دوباره install.sh را اجرا کنید (idempotent است)."
    else warn "گواهی گرفته نشد؛ سایت تا HTTPS نشود ورود را نمی‌پذیرد."; fi
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw status | grep -qE "Nginx Full|443" || { confirm "باز کردن پورت‌های ۸۰/۴۴۳ در ufw (ssh دست نمی‌خورد)؟" && run ufw allow 'Nginx Full'; }
  fi
fi

# ----------------------------------------------------------------- ۱۱. بررسی نهایی
step "بررسی نهایی"
if [ "$DRY_RUN" = 0 ]; then
  if have_systemd && systemctl is-active --quiet ravix.service; then
    for _ in $(seq 1 20); do
      if curl -fsS --unix-socket /run/ravix/gunicorn.sock -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" http://localhost/healthz >/dev/null 2>&1; then ok "/healthz پاسخ می‌دهد"; break; fi
      sleep 1
    done
  fi
  envsh "$VENV/bin/python" manage.py ravix_preflight || warn "ravix_preflight خطا/هشدار دارد؛ خروجی بالا را بخوانید (روی سرور تازه چند مورد طبیعی است)."
fi
SCHEME=https; [ "$SSL" = 0 ] && [ "$NGINX" = 0 ] && SCHEME=http
TOKEN="$(env_get RAVIX_INSTALL_TOKEN)"
cat <<EOF

${B}تمام شد.${N} ($CHANGES تغییر اعمال شد؛ لاگ: $LOG_FILE)

  گام آخر: صفحهٔ نصب را باز کنید و نام سایت، مدیرکل، زبان و منطقهٔ زمانی را بدهید:
      $SCHEME://$DOMAIN/install/
  توکن نصب:  ${TOKEN:-"(پس از اجرای واقعی در $ENV_FILE)"}
  (پس از نصب، آن صفحه برای همیشه بسته می‌شود. بدون وب: sudo -u $APP_USER ... manage.py ravix_install --help)

  پشتیبان روزانه: $BACKUP_DIR (ravix-backup.timer) — از ${ENV_FILE} جداگانه پشتیبان بگیرید؛ در پشتیبان خودکار نیست.
EOF
