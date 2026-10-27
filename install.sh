#!/usr/bin/env bash
# Installs Ravix on Ubuntu 22.04 / 24.04: Python, PostgreSQL, a system user, Gunicorn + systemd, Nginx and Let's Encrypt (certbot).
#
#   sudo ./install.sh                                   # interactive; asks before every change
#   sudo ./install.sh --domain example.com --email me@example.com --yes   # non-interactive
#   sudo ./install.sh --dry-run                         # only shows what it would do
#
# Idempotent: every step checks the state first and is skipped when done; running it again is safe.
# Secrets (database password, Django key, install token) are written only to /etc/ravix/ravix.env (mode 640) and never overwritten.
# Every message the operator sees is English here and is translated through t() from deploy/install.<lang>.tsv; the language is
# chosen with RAVIX_CLI_LANG=fa (or --lang fa), the same variable as the Python CLI. Guide: docs/installer.md
set -Eeuo pipefail
IFS=$'\n\t'

# ----------------------------------------------------------------- defaults and options
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
HERE="$SRC"   # where deploy/ (units and message catalogs) lives; --source only changes where the code is copied from

DOMAIN=""
LE_EMAIL=""
WWW=1
ASSUME_YES=0
DRY_RUN=0
SSL=1
NGINX=1
SYSTEMD=auto
CHANGES=0

# ----------------------------------------------------------------- language of the messages (RAVIX_CLI_LANG / --lang)
declare -A I18N=()
LANG_CODE="${RAVIX_CLI_LANG:-}"
for ((i = 1; i < $#; i++)); do [ "${!i}" = "--lang" ] && { j=$((i + 1)); LANG_CODE="${!j}"; }; done
LANG_CODE="${LANG_CODE//-/_}"
load_messages() {  # deploy/install.<lang>.tsv (msgid<TAB>translation); a language without a file simply stays English
  local code f k v
  for code in "$LANG_CODE" "${LANG_CODE%%_*}"; do
    [ -n "$code" ] && [ "$code" != en ] || continue
    f="$HERE/deploy/install.$code.tsv"
    [ -f "$f" ] || continue
    while IFS=$'\t' read -r k v || [ -n "$k" ]; do [ -n "$k" ] && I18N["$k"]="$v"; done < "$f"
    return 0
  done
}
t() {  # t "English message with %s" args... → the message in the operator's language (printf-style, no trailing newline)
  local m="$1"; shift
  [ -n "${I18N[$m]+x}" ] && m="${I18N[$m]}"
  # shellcheck disable=SC2059
  printf -- "$m" "$@"
}


usage() {
  t "Usage: sudo ./install.sh [options]"; echo
  t "  --domain D       the site's domain (without http://)"; echo
  t "  --email E        email for Let's Encrypt (expiry notices)"; echo
  t "  --no-www         do not add www.D to nginx / the certificate"; echo
  t "  --no-ssl         no certbot (only when TLS ends elsewhere: Cloudflare, a load balancer)"; echo
  t "  --no-nginx       do not touch nginx (you build the proxy yourself)"; echo
  t "  --no-systemd     do not create systemd services/timers (e.g. in a container)"; echo
  t "  --source DIR     path of the code (default: the folder of this script)"; echo
  t "  --lang CODE      language of these messages (same as RAVIX_CLI_LANG; 'fa' ships with Ravix)"; echo
  t "  -y, --yes        answer “yes” to every question (non-interactive)"; echo
  t "  -n, --dry-run    change nothing; only print"; echo
  t "  -h, --help"; echo
}

load_messages

while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="${2:?}"; shift 2;;
    --email) LE_EMAIL="${2:?}"; shift 2;;
    --no-www) WWW=0; shift;;
    --no-ssl) SSL=0; shift;;
    --no-nginx) NGINX=0; SSL=0; shift;;
    --no-systemd) SYSTEMD=no; shift;;
    --source) SRC="$(cd "${2:?}" && pwd)"; shift 2;;
    --lang) shift 2;;
    -y|--yes) ASSUME_YES=1; shift;;
    -n|--dry-run) DRY_RUN=1; shift;;
    -h|--help) usage; exit 0;;
    *) t "Unknown option: %s" "$1" >&2; echo >&2; usage >&2; exit 2;;
  esac
done

# ----------------------------------------------------------------- helpers (every message goes through t())
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
step() { printf '\n%s== %s%s\n' "$B" "$(t "$@")" "$N"; }
ok()   { printf '  %s✔%s %s\n' "$G" "$N" "$(t "$@")"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$(t "$@")" >&2; }
die()  { printf '%s✘ %s%s\n' "$R" "$(t "$@")" "$N" >&2; exit 1; }
trap 'die "Unexpected error at line %s (command: %s). Log: %s" "$LINENO" "$BASH_COMMAND" "$LOG_FILE"' ERR

run() {  # run a changing command; in dry-run only print it
  if [ "$DRY_RUN" = 1 ]; then (IFS=' '; printf '  [dry-run] %s\n' "$*"); return 0; fi
  CHANGES=$((CHANGES + 1)); "$@"
}

confirm() {  # confirm "question" args... → 0 = yes
  local q; q="$(t "$@")"
  if [ "$ASSUME_YES" = 1 ] || [ "$DRY_RUN" = 1 ]; then printf '  ? %s  [%s]\n' "$q" "$(t "yes")"; return 0; fi
  [ -t 0 ] || die "Input is not interactive; run with --yes or from a terminal."
  local a; read -r -p "  ? $q [y/N] " a; [[ "$a" =~ ^([yY]|yes|بله)$ ]]
}

ask() {  # ask VAR "question" [default]
  local __var="$1" __q __def="${3:-}" __a=""
  __q="$(t "$2")"
  if [ -n "${!__var}" ]; then return 0; fi
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then printf -v "$__var" '%s' "$__def"; return 0; fi
  read -r -p "  ? $__q${__def:+ [$__def]} " __a; printf -v "$__var" '%s' "${__a:-$__def}"
}

rand_hex() { openssl rand -hex "$1"; }
as_app() { runuser -u "$APP_USER" -- "$@"; }
envsh() {  # run a command as the service user with the variables of ravix.env
  runuser -u "$APP_USER" -- bash -c 'set -a; . "$1"; set +a; shift; cd '"$APP_DIR"'; exec "$@"' _ "$ENV_FILE" "$@"
}
env_get() { [ -r "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }
env_set() {  # env_set KEY VALUE — adds the key only if missing; never changes an existing value
  if [ -n "$(env_get "$1")" ]; then return 0; fi
  [ "$DRY_RUN" = 1 ] && { printf '  [dry-run] '; t "%s ← (would be set)" "$1"; echo; return 0; }
  printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; CHANGES=$((CHANGES + 1))
}
have_systemd() {
  case "$SYSTEMD" in no) return 1;; esac
  [ -d /run/systemd/system ]
}
pkg_missing() { local p; for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done; }

# ----------------------------------------------------------------- helpers: HTTP/2 and the install token (tested by apps/core/tests/test_install_script_logic.py)
nginx_version() { nginx -v 2>&1 | sed -n 's|.*nginx/\([0-9][0-9.]*\).*|\1|p'; }
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]; }   # version_ge A B → A >= B
nginx_enable_http2() {  # nginx_enable_http2 FILE VERSION: turns HTTP/2 on in every "listen ... 443 ssl" server block. 0 = changed, 1 = nothing to do
  local f="$1" v="${2:-}" tmp
  grep -qE '^[[:space:]]*listen[[:space:]]+[^;#]*443[^;#]*[[:space:]]ssl' "$f" || return 1   # no HTTPS block yet (certbot has not run)
  if [ -n "$v" ] && version_ge "$v" 1.25.1; then   # nginx >= 1.25.1: the separate "http2 on;" directive ("listen ... http2" is deprecated)
    grep -qE '^[[:space:]]*http2[[:space:]]+on[[:space:]]*;' "$f" && return 1
    tmp="$(mktemp)"
    awk '/^[[:space:]]*server[[:space:]]*\{/ { seen = 0 }
         { print }
         /^[[:space:]]*listen[[:space:]]+[^;#]*443[^;#]*[[:space:]]ssl[^;#]*;/ && !seen { match($0, /^[[:space:]]*/); print substr($0, 1, RLENGTH) "http2 on;"; seen = 1 }' "$f" > "$tmp"
    cat "$tmp" > "$f"; rm -f "$tmp"
  else   # older nginx (Ubuntu 22.04 has 1.18, 24.04 has 1.24): the "http2" parameter of listen
    grep -E '^[[:space:]]*listen[[:space:]]+[^;#]*443[^;#]*[[:space:]]ssl' "$f" | grep -qv 'http2' || return 1
    sed -i -E '/^[[:space:]]*listen[[:space:]]+[^;#]*443[^;#]*[[:space:]]ssl/{/http2/!s/\bssl\b/ssl http2/}' "$f"
  fi
}
site_installed() {  # 0 = the site is already installed (read-only check against the real database; any failure means "not installed")
  [ -f "$ENV_FILE" ] && [ -x "$VENV/bin/python" ] && [ -n "$(env_get DATABASE_URL)" ] || return 1
  envsh "$VENV/bin/python" -c 'import sys, django; django.setup(); from apps.accounts.models import User; from apps.core.models import SiteSettings; sys.exit(0 if (SiteSettings.objects.filter(pk=1, installed_at__isnull=False).exists() or User.objects.exists()) else 1)' >/dev/null 2>&1
}
forget_install_token() {  # removes RAVIX_INSTALL_TOKEN from ravix.env (the install page is closed for good once the site is installed)
  [ -f "$ENV_FILE" ] && grep -q '^RAVIX_INSTALL_TOKEN=' "$ENV_FILE" || return 0
  if [ "$DRY_RUN" = 1 ]; then printf '  [dry-run] '; t "%s would be removed from %s" RAVIX_INSTALL_TOKEN "$ENV_FILE"; echo; return 0; fi
  run sed -i '/^RAVIX_INSTALL_TOKEN=/d' "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"
  ok "The site is already installed: %s was removed from %s" RAVIX_INSTALL_TOKEN "$ENV_FILE"
}

# ----------------------------------------------------------------- 0. preliminary checks
step "Checking prerequisites"
[ "$(id -u)" = 0 ] || die "Run as root: sudo ./install.sh"
if [ "$DRY_RUN" = 0 ]; then mkdir -p "$(dirname "$LOG_FILE")"; exec > >(tee -a "$LOG_FILE") 2>&1; fi
. /etc/os-release 2>/dev/null || true
UNKNOWN_OS="$(t "unknown")"
if [ "${ID:-}" != "ubuntu" ] || { [ "${VERSION_ID:-}" != "22.04" ] && [ "${VERSION_ID:-}" != "24.04" ]; }; then
  if [ "${RAVIX_SKIP_OS_CHECK:-0}" = 1 ]; then warn "The system %s is not supported; continuing because of RAVIX_SKIP_OS_CHECK=1" "${PRETTY_NAME:-$UNKNOWN_OS}"
  else die "Only Ubuntu 22.04 and 24.04 are supported (this system: %s)." "${PRETTY_NAME:-$UNKNOWN_OS}"; fi
fi
ok "Operating system: %s" "${PRETTY_NAME:-?}"
[ -f "$SRC/manage.py" ] && [ -f "$SRC/requirements.lock" ] || die "The Ravix code was not found in %s (manage.py / requirements.lock). Pass --source." "$SRC"
ok "Code: %s" "$SRC"
if have_systemd; then ok "systemd is active"; else warn "systemd is not running; no service/timer is created (normal in a container)"; SYSTEMD=no; fi

if [ "$NGINX" = 1 ] || [ "$SSL" = 1 ]; then
  ask DOMAIN "The site's domain (e.g. example.com):"
  [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "The domain is invalid or empty (--domain)."
else
  ask DOMAIN "Host name / domain for ALLOWED_HOSTS:" "localhost"
fi
if [ "$SSL" = 1 ]; then ask LE_EMAIL "Email for Let's Encrypt (empty = no email):"; fi
[ "$SSL" = 0 ] && [ "$NGINX" = 1 ] && warn "Without TLS: session cookies are “Secure” in production; login works only behind HTTPS (another TLS terminator)."

CERTBOT_PKG=""; [ "$SSL" = 1 ] && CERTBOT_PKG=", certbot"
echo
t "  With your confirmation at each step, this script will:"; echo
t "    • install the missing apt packages (python3-venv, postgresql, nginx%s, rsync, openssl)" "$CERTBOT_PKG"; echo
t "    • create the system user “%s” and folders in %s, settings in %s" "$APP_USER" "$BASE" "$ENV_DIR"; echo
t "    • copy the code to %s and build the virtualenv in %s" "$APP_DIR" "$VENV"; echo
t "    • create the PostgreSQL role/database “%s” with a random password" "$DB_NAME"; echo
t "    • run migrate, collectstatic, the Gunicorn service and the systemd timers"; echo
if [ "$SSL" = 1 ]; then t "    • set up the nginx site for “%s” and a Let's Encrypt certificate" "$DOMAIN"; else t "    • set up the nginx site for “%s”" "$DOMAIN"; fi; echo
confirm "Continue?" || { t "Cancelled; nothing was changed."; echo; exit 0; }

# ----------------------------------------------------------------- 1. apt packages
step "System packages"
PKGS=(python3 python3-venv ca-certificates curl openssl rsync postgresql postgresql-client)
[ "$NGINX" = 1 ] && PKGS+=(nginx)
[ "$SSL" = 1 ] && PKGS+=(certbot python3-certbot-nginx)
mapfile -t MISSING < <(pkg_missing "${PKGS[@]}")
if [ "${#MISSING[@]}" -gt 0 ] && [ -n "${MISSING[0]}" ]; then
  printf '  '; t "Missing packages: %s" "$(IFS=' '; echo "${MISSING[*]}")"; echo
  confirm "Install with apt?" || die "Cannot continue without these packages."
  run apt-get update -y
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${MISSING[@]}"
else
  ok "All required packages are installed"
fi

# Python >= 3.11 (Ubuntu 22.04 only has 3.10 => python3.12 from deadsnakes, with your confirmation)
PYBIN=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PYBIN="$(command -v "$c")"; break; fi
done
if [ -z "$PYBIN" ]; then
  warn "Python 3.11 or newer was not found (Ubuntu %s has an older version)." "${VERSION_ID:-?}"
  confirm "Add the deadsnakes repository and install python3.12?" || die "Python >= 3.11 is required."
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends software-properties-common
  run add-apt-repository -y ppa:deadsnakes/ppa
  run apt-get update -y
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3.12 python3.12-venv
  PYBIN=/usr/bin/python3.12
fi
ok "Python: %s (%s)" "$PYBIN" "$("$PYBIN" -V 2>&1 || echo '?')"

# ----------------------------------------------------------------- 2. user and folders
# Ownership model (deploy/*.service must match it; apps/core/tests/test_deploy_permissions.py checks both):
#   code and plugins/  root:ravix 750   read-only for the service; the units therefore need SupplementaryGroups=ravix
#   media              ravix:www-data 2750   nginx (www-data) reads it
#   cache folder       ravix:www-data 750
#   /etc/ravix         root:ravix 750, ravix.env 640
step "System user and folders"
if id "$APP_USER" >/dev/null 2>&1; then ok "The user %s exists" "$APP_USER"
else
  confirm "Create the system user “%s” (no login, home=%s)?" "$APP_USER" "$BASE" || die "The user is required."
  run adduser --system --group --home "$BASE" --shell /usr/sbin/nologin "$APP_USER"
fi
if getent group "$APP_USER" | grep -qw www-data; then ok "www-data is a member of the group %s" "$APP_USER"
else
  [ "$NGINX" = 1 ] && { confirm "Add www-data to the group %s (to read the socket/media)?" "$APP_USER" && run usermod -aG "$APP_USER" www-data; }
fi
CACHE_DIR=/var/cache/ravix   # file cache shared by the workers (systemd also creates it with CacheDirectory=)
DIRS=(app media shop_private plugin_data plugins_installed plugin_backups themes_installed registry_packages update_work update_backups)
need_dirs=0; for d in "${DIRS[@]}"; do [ -d "$BASE/$d" ] || need_dirs=1; done
[ -d "$ENV_DIR" ] && [ -d "$BACKUP_DIR" ] && [ -d "$CACHE_DIR" ] || need_dirs=1
if [ "$need_dirs" = 1 ]; then
  confirm "Create the folders under %s, %s and %s?" "$BASE" "$ENV_DIR" "$BACKUP_DIR" || die "The folders are required."
  for d in "${DIRS[@]}"; do run mkdir -p "$BASE/$d"; done
  run mkdir -p "$ENV_DIR" "$BACKUP_DIR" "$CACHE_DIR"
else ok "The folders exist"
fi
if [ "$DRY_RUN" = 0 ]; then
  chown "$APP_USER:$APP_USER" "$BASE"; chmod 750 "$BASE"
  chown "$APP_USER:www-data" "$BASE/media"; chmod 2750 "$BASE/media"
  for d in shop_private plugin_data plugins_installed themes_installed registry_packages update_work update_backups; do chown "$APP_USER:$APP_USER" "$BASE/$d"; chmod 750 "$BASE/$d"; done
  chown "$APP_USER:$APP_USER" "$BASE/plugin_backups"; chmod 700 "$BASE/plugin_backups"
  chown "$APP_USER:$APP_USER" "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  chown "$APP_USER:www-data" "$CACHE_DIR"; chmod 750 "$CACHE_DIR"
  chown root:"$APP_USER" "$ENV_DIR"; chmod 750 "$ENV_DIR"
fi

# ----------------------------------------------------------------- 3. code
step "Application code"
if [ "$SRC" = "$APP_DIR" ]; then ok "The code is already in %s" "$APP_DIR"
else
  do_sync=0
  if [ -f "$APP_DIR/manage.py" ]; then
    t "  A previous installation was found; only the code files are synchronised over it (site data, media and settings are untouched)."; echo
    confirm "Update the code in %s (rsync without deleting)?" "$APP_DIR" && do_sync=1
  else
    confirm "Copy the code from %s to %s?" "$SRC" "$APP_DIR" && do_sync=1
  fi
  if [ "$do_sync" = 1 ]; then
    run rsync -a --exclude='.git' --exclude='.github' --exclude='.venv' --exclude='venv' --exclude='__pycache__' --exclude='*.pyc' \
      --exclude='db.sqlite3' --exclude='*.sqlite3' --exclude='.env' --exclude='.env.*' --exclude='/media' --exclude='/staticfiles' \
      --exclude='/shop_private' --exclude='/plugin_data' --exclude='/plugins_installed' --exclude='/plugin_backups' --exclude='/themes_installed' \
      --exclude='/registry_packages' --exclude='/update_work' --exclude='/update_backups' --exclude='/install_token' \
      --exclude='/dist' --exclude='/keys' --exclude='*.key' --exclude='/certs' "$SRC"/ "$APP_DIR"/
  elif [ ! -f "$APP_DIR/manage.py" ]; then die "The code is required."
  else warn "The code was not updated"; fi
fi
if [ "$DRY_RUN" = 0 ] && [ -d "$APP_DIR" ]; then
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
  # bundled plugins: read-only for the service user (as in docs/deployment.md). Because the folder is root:ravix 750, the units
  # MUST give the process the group ravix (SupplementaryGroups=ravix), or listing it fails with PermissionError.
  [ -d "$APP_DIR/plugins" ] && { chown -R root:"$APP_USER" "$APP_DIR/plugins"; chmod -R g-w,o-rwx "$APP_DIR/plugins"; }
  [ -e "$APP_DIR/media" ] || ln -s "$BASE/media" "$APP_DIR/media"   # MEDIA_ROOT = BASE_DIR/media
  chown -h "$APP_USER:$APP_USER" "$APP_DIR/media" 2>/dev/null || true
fi

CODE="$APP_DIR"; [ -d "$APP_DIR/deploy" ] || CODE="$SRC"   # in dry-run the code has not been copied yet

# ----------------------------------------------------------------- 4. virtualenv and dependencies
step "virtualenv and dependencies"
LOCK_SUM="$(sha256sum "$SRC/requirements.lock" | cut -c1-64)"
if [ -x "$VENV/bin/python" ]; then ok "The virtualenv exists"
else
  confirm "Create the virtualenv in %s?" "$VENV" || die "The virtualenv is required."
  run as_app "$PYBIN" -m venv "$VENV"
fi
if [ -f "$VENV/.requirements.sha256" ] && [ "$(cat "$VENV/.requirements.sha256")" = "$LOCK_SUM" ]; then ok "The dependencies match requirements.lock"
else
  confirm "Install the dependencies from requirements.lock with pip?" || die "The dependencies are required."
  run as_app "$VENV/bin/pip" install --disable-pip-version-check -r "$APP_DIR/requirements.lock"
  [ "$DRY_RUN" = 1 ] || echo "$LOCK_SUM" | as_app tee "$VENV/.requirements.sha256" >/dev/null
fi

# ----------------------------------------------------------------- 5. PostgreSQL
step "PostgreSQL"
if have_systemd; then run systemctl enable --now postgresql
elif ! pg_isready -q 2>/dev/null; then
  warn "PostgreSQL is not running and there is no systemd; start it yourself (e.g. pg_ctlcluster)."
fi
psql_admin() { runuser -u postgres -- psql -v ON_ERROR_STOP=1 -X -q "$@"; }
if [ "$DRY_RUN" = 1 ] && ! pg_isready -q 2>/dev/null; then
  warn "[dry-run] PostgreSQL is not up yet; the role/database creation is only printed"; ROLE_EXISTS=""; DB_EXISTS=""
else
  pg_isready -q || die "PostgreSQL does not respond."
  ROLE_EXISTS="$(psql_admin -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")"
  DB_EXISTS="$(psql_admin -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")"
fi
DB_PASS=""
if [ -n "$(env_get DATABASE_URL)" ]; then ok "DATABASE_URL is already in %s" "$ENV_FILE"
else
  DB_PASS="$(rand_hex 24)"
  if [ -z "$ROLE_EXISTS" ]; then
    confirm "Create the PostgreSQL role “%s” with a random password?" "$DB_USER" || die "The database role is required."
    [ "$DRY_RUN" = 1 ] || printf "CREATE ROLE %s LOGIN PASSWORD :'pw';\n" "$DB_USER" | psql_admin -v pw="$DB_PASS"
  else
    warn "The role “%s” exists but its password is not in %s." "$DB_USER" "$ENV_FILE"
    confirm "Replace the role's password with a new random one? (other applications using this role will break)" || die "Cannot continue without the role's password."
    [ "$DRY_RUN" = 1 ] || printf "ALTER ROLE %s LOGIN PASSWORD :'pw';\n" "$DB_USER" | psql_admin -v pw="$DB_PASS"
  fi
fi
if [ -z "$DB_EXISTS" ]; then
  confirm "Create the database “%s” (owner %s, UTF8)?" "$DB_NAME" "$DB_USER" || die "The database is required."
  [ "$DRY_RUN" = 1 ] || psql_admin -c "CREATE DATABASE $DB_NAME OWNER $DB_USER ENCODING 'UTF8' TEMPLATE template0"
else ok "The database %s exists" "$DB_NAME"; fi

# ----------------------------------------------------------------- 6. environment settings
step "Settings (%s)" "$ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  confirm "Create %s (mode 640, random secrets)?" "$ENV_FILE" || die "The settings file is required."
  [ "$DRY_RUN" = 1 ] || { install -m 640 -o root -g "$APP_USER" /dev/null "$ENV_FILE"; printf '# created by install.sh — never put secrets in the code repository\n' >> "$ENV_FILE"; }
fi
ALLOWED="$DOMAIN"; ORIGINS="https://$DOMAIN"
if [ "$WWW" = 1 ] && [ "$NGINX" = 1 ] && [[ "$DOMAIN" == *.* ]]; then ALLOWED="$DOMAIN,www.$DOMAIN"; ORIGINS="https://$DOMAIN,https://www.$DOMAIN"; fi
env_set DJANGO_SETTINGS_MODULE config.settings.production
env_set DJANGO_SECRET_KEY "$("$PYBIN" -c 'import secrets; print(secrets.token_urlsafe(64))')"
env_set DJANGO_ALLOWED_HOSTS "$ALLOWED"
env_set DJANGO_CSRF_TRUSTED_ORIGINS "$ORIGINS"
[ -n "$DB_PASS" ] && env_set DATABASE_URL "postgres://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME"
env_set RAVIX_SITE_URL "https://$DOMAIN"
# the install token only exists until the site is installed: an installed site never gets a new one (and loses an old one)
if site_installed; then forget_install_token; else env_set RAVIX_INSTALL_TOKEN "$(rand_hex 16)"; fi
env_set RAVIX_SHOP_PRIVATE_ROOT "$BASE/shop_private"
env_set RAVIX_PLUGIN_DATA_DIR "$BASE/plugin_data"
[ "$NGINX" = 1 ] && env_set RAVIX_ACCEL_REDIRECT_PREFIX "/_plugin_files/"   # nginx sends plugin downloads (the internal location in deploy/nginx-ravix.conf)
env_set RAVIX_PLUGIN_INSTALL_DIR "$BASE/plugins_installed"
env_set RAVIX_PLUGIN_BACKUP_DIR "$BASE/plugin_backups"
env_set RAVIX_THEME_INSTALL_DIR "$BASE/themes_installed"
env_set RAVIX_REGISTRY_STORAGE "$BASE/registry_packages"
env_set RAVIX_UPDATE_WORK_DIR "$BASE/update_work"
# client IP: Gunicorn is on a unix socket and is trusted automatically; nothing is needed (RAVIX_TRUSTED_PROXIES is only for a proxy on TCP)
[ "$SSL" = 0 ] && env_set DJANGO_SECURE_SSL_REDIRECT false
[ "$DRY_RUN" = 1 ] || { chown root:"$APP_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"; }
ok "The settings are ready (existing keys were not touched)"

# ----------------------------------------------------------------- 7. migrate / static
step "migrate and static files"
if [ "$DRY_RUN" = 1 ]; then
  echo "  [dry-run] manage.py migrate --noinput && collectstatic --noinput"
else
  if confirm "Run migrate and collectstatic?"; then
    envsh "$VENV/bin/python" manage.py migrate --noinput
    envsh "$VENV/bin/python" manage.py collectstatic --noinput >/dev/null
    ok "migrate and collectstatic are done"
  fi
fi

# ----------------------------------------------------------------- 8. systemd
step "Services (systemd)"
if have_systemd; then
  UNITS=(ravix.service ravix-publish-scheduled.service ravix-publish-scheduled.timer ravix-backup.service ravix-backup.timer ravix-expire-orders.service ravix-expire-orders.timer)
  changed=0
  for u in "${UNITS[@]}"; do cmp -s "$CODE/deploy/$u" "/etc/systemd/system/$u" 2>/dev/null || changed=1; done
  if [ "$changed" = 1 ]; then
    confirm "Install the unit files in /etc/systemd/system?" || die "The service is required."
    for u in "${UNITS[@]}"; do run install -m 644 "$CODE/deploy/$u" "/etc/systemd/system/$u"; done
    run systemctl daemon-reload
  else ok "The units are up to date"; fi
  confirm "Enable and start ravix.service and the timers?" && {
    run systemctl enable --now ravix.service ravix-publish-scheduled.timer ravix-backup.timer
    grep -qi '^RAVIX_SHOP_ENABLED=false' "$ENV_FILE" || run systemctl enable --now ravix-expire-orders.timer
    run systemctl restart ravix.service
  }
else
  warn "Skipped (no systemd). Run manually: cd %s && gunicorn config.wsgi:application -c deploy/gunicorn.conf.py" "$APP_DIR"
fi

# ----------------------------------------------------------------- 9. nginx
if [ "$NGINX" = 1 ]; then
  step "Nginx"
  SITE=/etc/nginx/sites-available/ravix
  SERVER_NAMES="$DOMAIN"; [ "$WWW" = 1 ] && [[ "$DOMAIN" == *.* ]] && SERVER_NAMES="$DOMAIN www.$DOMAIN"
  TMP_SITE="$(mktemp)"
  sed "s/server_name example.com www.example.com;/server_name $SERVER_NAMES;/" "$CODE/deploy/nginx-ravix.conf" > "$TMP_SITE"
  PROXY_SRC="$CODE/deploy/ravix-proxy.conf"
  # a kernel without IPv6 (seen in some containers/VPSs): `listen [::]` makes nginx -t fail
  [ -e /proc/net/if_inet6 ] || { sed -i '/listen \[::\]/d' "$TMP_SITE"; warn "IPv6 is not enabled on this system; the listen [::] lines were removed"; }
  if [ "$SSL" = 0 ]; then  # TLS ends elsewhere: accept the upstream X-Forwarded-Proto
    { printf 'map $http_x_forwarded_proto $ravix_proto { default $scheme; https https; }\n'; cat "$TMP_SITE"; } > "$TMP_SITE.2" && mv "$TMP_SITE.2" "$TMP_SITE"
    PROXY_SRC="$(mktemp)"; sed 's/\$scheme/$ravix_proto/' "$CODE/deploy/ravix-proxy.conf" > "$PROXY_SRC"
  fi
  if cmp -s "$TMP_SITE" "$SITE" 2>/dev/null && cmp -s "$PROXY_SRC" /etc/nginx/ravix-proxy.conf 2>/dev/null; then ok "The nginx configuration is up to date"
  else
    if [ -f "$SITE" ] && grep -q "managed by Certbot" "$SITE" 2>/dev/null; then
      warn "certbot has modified %s; not overwriting it (docs/installer.md explains how to apply the newer nginx template)." "$SITE"
    else
      confirm "Install the nginx configuration for %s (an existing file is saved with a .bak suffix)?" "$SERVER_NAMES" && {
        [ -f "$SITE" ] && run cp -a "$SITE" "$SITE.bak.$(date +%s)"
        run install -m 644 "$PROXY_SRC" /etc/nginx/ravix-proxy.conf
        run install -m 644 "$TMP_SITE" "$SITE"
        run ln -sfn "$SITE" /etc/nginx/sites-enabled/ravix
        if [ -e /etc/nginx/sites-enabled/default ]; then confirm "Remove nginx's default site (sites-enabled/default)?" && run rm -f /etc/nginx/sites-enabled/default; fi
      }
    fi
  fi
  rm -f "$TMP_SITE"
  if [ "$DRY_RUN" = 0 ]; then
    nginx -t
    if have_systemd; then systemctl enable --now nginx && systemctl reload nginx; else warn "Reload nginx yourself"; fi
  fi
fi

# ----------------------------------------------------------------- 10. Let's Encrypt
if [ "$SSL" = 1 ]; then
  step "Let's Encrypt certificate"
  if [ -e "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then ok "A certificate for %s already exists (certbot renews it automatically)" "$DOMAIN"
  else
    resolved="$(getent ahostsv4 "$DOMAIN" | awk '{print $1; exit}' || true)"
    mine=" $(hostname -I 2>/dev/null) "
    if [ -z "$resolved" ]; then warn "The DNS of %s does not resolve yet; certbot will fail." "$DOMAIN"
    elif [[ "$mine" != *" $resolved "* ]]; then warn "The DNS points to %s, which is not a local IP of this server (may be normal behind NAT/a CDN)." "$resolved"
    else ok "The DNS points to this server (%s)" "$resolved"; fi
    CB=(certbot --nginx --non-interactive --agree-tos --no-eff-email --redirect -d "$DOMAIN")
    [ "$WWW" = 1 ] && [[ "$DOMAIN" == *.* ]] && CB+=(-d "www.$DOMAIN")
    if [ -n "$LE_EMAIL" ]; then CB+=(-m "$LE_EMAIL"); else CB+=(--register-unsafely-without-email); fi
    printf '  '; t "Command: %s" "$(IFS=' '; echo "${CB[*]}")"; echo
    if confirm "Get a certificate with certbot? (port 80 must be open to the internet and the DNS correct)"; then
      run "${CB[@]}" || warn "certbot failed; run install.sh again once the DNS is right (it is idempotent)."
    else warn "No certificate was obtained; the site will not accept logins until HTTPS works."; fi
  fi
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw status | grep -qE "Nginx Full|443" || { confirm "Open ports 80/443 in ufw (ssh is not touched)?" && run ufw allow 'Nginx Full'; }
  fi
fi

# ----------------------------------------------------------------- 10b. HTTP/2 (needs the HTTPS block certbot adds)
if [ "$NGINX" = 1 ] && [ "$SSL" = 1 ] && [ "$DRY_RUN" = 0 ] && [ -f "${SITE:-/nonexistent}" ]; then
  H2_TMP="$(mktemp)"; cp "$SITE" "$H2_TMP"
  if nginx_enable_http2 "$H2_TMP" "$(nginx_version)"; then
    if confirm "Enable HTTP/2 in %s (a backup copy is kept)?" "$SITE"; then
      H2_BAK="$SITE.bak.$(date +%s)"
      run cp -a "$SITE" "$H2_BAK"
      run install -m 644 "$H2_TMP" "$SITE"
      if nginx -t >/dev/null 2>&1; then
        if have_systemd; then systemctl reload nginx; fi
        ok "HTTP/2 is enabled"
      else
        run cp -a "$H2_BAK" "$SITE"
        warn "nginx rejected the HTTP/2 change; the previous configuration was restored."
      fi
    fi
  else ok "HTTP/2 is enabled (or the HTTPS block does not exist yet)"; fi
  rm -f "$H2_TMP"
fi

# ----------------------------------------------------------------- 11. final check
step "Final check"
if [ "$DRY_RUN" = 0 ]; then
  if have_systemd && systemctl is-active --quiet ravix.service; then
    for _ in $(seq 1 20); do
      if curl -fsS --unix-socket /run/ravix/gunicorn.sock -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" http://localhost/healthz >/dev/null 2>&1; then ok "/healthz responds"; break; fi
      sleep 1
    done
  fi
  envsh "$VENV/bin/python" manage.py ravix_preflight || warn "ravix_preflight reports errors/warnings; read the output above (a few are normal on a fresh server)."
fi
SCHEME=https; [ "$SSL" = 0 ] && [ "$NGINX" = 0 ] && SCHEME=http
INSTALLED=0
if site_installed; then INSTALLED=1; forget_install_token; fi
TOKEN="$(env_get RAVIX_INSTALL_TOKEN)"
[ -n "$TOKEN" ] || TOKEN="$(t "(in %s after a real run)" "$ENV_FILE")"
echo
printf '%s' "$B"; t "Done."; printf '%s ' "$N"; t "(%s changes applied; log: %s)" "$CHANGES" "$LOG_FILE"; echo
echo
if [ "$INSTALLED" = 1 ]; then
  t "  The site is already installed. Sign in to the panel:"; echo
  echo "      $SCHEME://$DOMAIN/panel/"
else
  t "  Last step: open the install page and enter the site name, the super admin, the language and the time zone:"; echo
  echo "      $SCHEME://$DOMAIN/install/"
  t "  Install token:  %s" "$TOKEN"; echo
  t "  (after the install that page is closed for good. Without the web: sudo -u %s ... manage.py ravix_install --help)" "$APP_USER"; echo
  t "  After installing, run “sudo ./install.sh” again (or delete the RAVIX_INSTALL_TOKEN line in %s): the token is then removed from the settings." "$ENV_FILE"; echo
fi
echo
t "  Daily backup: %s (ravix-backup.timer) — back up %s separately; it is not part of the automatic backup." "$BACKUP_DIR" "$ENV_FILE"; echo
