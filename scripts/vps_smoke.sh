#!/usr/bin/env bash
# آزمون دودی استقرار Ravix روی یک سرور تازهٔ Ubuntu 22.04/24.04 — مراحل docs/deployment.md را واقعاً اجرا می‌کند.
# راهنمای کامل اجرا: docs/vps-smoke-runbook.md
#
# ⚠ فقط روی یک سرور آزمایشی و تازه. این اسکریپت بسته نصب می‌کند، کاربر `ravin` و دیتابیس `ravin` می‌سازد،
#   رمز نقش دیتابیس را تنظیم می‌کند، nginx را تنظیم می‌کند و در چرخهٔ بازیابی، دیتابیس `ravin` را با پشتیبانِ همان لحظه جایگزین می‌کند.
#   به همین دلیل اگر سرور «تازه» نباشد (ravin.env، media غیرخالی، جدول در دیتابیس ravin، سایت nginx غیر از default، …) بدون هیچ تغییری متوقف می‌شود.
#   هیچ گزینه‌ای برای دور زدن این بررسی نیست. اجرای دوباره روی سروری که همین اسکریپت ساخته مجاز است (فایل /etc/ravin/.vps-smoke-owned).
# ⚠ این اسکریپت هنوز روی هیچ VPS واقعی اجرا نشده است؛ خروجی اجرای شما همان چیزی است که باید برای بررسی بفرستید.
#
# اجرا (به‌عنوان root، از داخل پوشهٔ بازشدهٔ پروژه):
#   sudo bash scripts/vps_smoke.sh                                             # حالت «محلی»: بدون دامنه/certbot، HTTPS اجباری خاموش
#   sudo DOMAIN=test.example.com CERTBOT_EMAIL=you@example.com bash scripts/vps_smoke.sh   # حالت کامل (DNS باید از قبل به این سرور اشاره کند)
#   اختیاری: CERTBOT_STAGING=1 (Let's Encrypt آزمایشی)، SMOKE_UFW=1 (فعال‌کردن ufw؛ بدون آن ufw دست نمی‌خورد)
#
# خروجی: خلاصهٔ PASS/FAIL در انتها + گزارش کامل در /root/ravin-vps-smoke.log (mode 600؛ رمز و کلید در آن نمی‌آید).
# پایان کار: فقط دادهٔ آزمایشی که خود اسکریپت ساخته (کاربران smoke/marker و فایل‌های smoke.txt/marker.txt) حذف می‌شود؛ نصب برای بررسی شما می‌ماند.
set -uo pipefail

LOG=/root/ravin-vps-smoke.log
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP=/srv/ravin/app
ENVF=/etc/ravin/ravin.env
SENTINEL=/etc/ravin/.vps-smoke-owned
PY=/srv/ravin/venv/bin/python
DOMAIN="${DOMAIN:-}"
HOST_HDR="${DOMAIN:-localhost}"
RESULTS=()
FAILED=0

[ "$(id -u)" = 0 ] || { echo "باید با root اجرا شود (sudo)"; exit 2; }
[ -f "$SRC/manage.py" ] || { echo "manage.py کنار اسکریپت پیدا نشد؛ از داخل پروژه اجرا کنید"; exit 2; }
if [ -n "$DOMAIN" ] && [ -z "${CERTBOT_EMAIL:-}" ]; then echo "در حالت DOMAIN باید CERTBOT_EMAIL هم داده شود"; exit 2; fi
case "$DOMAIN" in *[!A-Za-z0-9.-]*) echo "DOMAIN نامعتبر است"; exit 2 ;; esac
[ -L "$LOG" ] && { echo "$LOG یک symlink است؛ پاکش کنید"; exit 2; }

# ── بررسی «سرور تازه» — پیش از هر تغییر ─────────────────────────────────────
fresh_host_check() {
  local why=()
  [ -e "$SENTINEL" ] && return 0                      # اجرای دوباره روی سرور ساخته‌شده با همین اسکریپت
  [ -e "$ENVF" ] && why+=("$ENVF از قبل هست")
  [ -f "$APP/manage.py" ] && why+=("$APP از قبل کد دارد")
  [ -d /srv/ravin/media ] && [ -n "$(ls -A /srv/ravin/media 2>/dev/null)" ] && why+=("/srv/ravin/media خالی نیست")
  [ -d /var/backups/ravin ] && [ -n "$(ls -A /var/backups/ravin 2>/dev/null)" ] && why+=("/var/backups/ravin خالی نیست")
  [ -e /etc/systemd/system/ravin.service ] && why+=("سرویس ravin از قبل نصب است")
  if [ -d /etc/nginx/sites-enabled ]; then
    local f extra=""
    for f in /etc/nginx/sites-enabled/* /etc/nginx/sites-enabled/.[!.]*; do
      [ -e "$f" ] || [ -L "$f" ] || continue
      [ "$(basename "$f")" = default ] || extra="$extra $(basename "$f")"
    done
    [ -n "$extra" ] && why+=("سایت(های) nginx غیر از default فعال است:$extra")
  fi
  if command -v psql >/dev/null 2>&1 && pg_isready -q 2>/dev/null; then
    if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='ravin'" 2>/dev/null | grep -q 1; then
      local n; n="$(sudo -u postgres psql -d ravin -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null || echo "?")"
      [ "$n" != "0" ] && why+=("دیتابیس ravin از قبل هست (جدول‌ها: $n)")
    fi
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='ravin'" 2>/dev/null | grep -q 1 && why+=("نقش ravin در PostgreSQL از قبل هست")
  fi
  if [ "${#why[@]}" -gt 0 ]; then
    echo "این سرور «تازه» نیست؛ برای محافظت از داده‌ها هیچ تغییری داده نشد:"; printf '  - %s\n' "${why[@]}"
    echo "این اسکریپت فقط برای سرور آزمایشی تازه است (جزئیات: docs/vps-smoke-runbook.md)."
    exit 3
  fi
}
fresh_host_check

: > "$LOG"; chmod 600 "$LOG"
. /etc/os-release 2>/dev/null || true
{
  echo "سیستم‌عامل: ${PRETTY_NAME:-نامشخص} | هسته: $(uname -r) | حالت: ${DOMAIN:+کامل ($DOMAIN)}${DOMAIN:-محلی} | $(date -u +%FT%TZ)"
  echo "RAM: $(free -m | awk '/Mem:/{print $2}')MB | دیسک آزاد /srv: $(df -h --output=avail /srv 2>/dev/null | tail -1 | tr -d ' ')"
} | tee -a "$LOG"
if [ -n "$DOMAIN" ]; then
  ips="$(getent ahostsv4 "$DOMAIN" | awk '{print $1}' | sort -u | tr '\n' ' ')"
  echo "DNS $DOMAIN → ${ips:-(حل نشد)} | آدرس‌های این سرور: $(hostname -I 2>/dev/null)" | tee -a "$LOG"
  if [ -z "$ips" ]; then echo "خطا: دامنه حل نمی‌شود؛ DNS را درست کنید"; exit 2; fi
  case " $(hostname -I 2>/dev/null) " in *" ${ips%% *} "*) ;; *) echo "هشدار: آی‌پی دامنه روی این سرور نیست (پشت NAT/پروکسی ممکن است عادی باشد؛ اگر نه، certbot شکست می‌خورد)" | tee -a "$LOG" ;; esac
fi

# اجرای یک مرحله؛ خروجی فقط در لاگ می‌رود. $1=نام مرحله، بقیه=دستور (با bash -c)
step() {
  local name="$1" rc; shift
  echo "=== $name ===" >> "$LOG"
  bash -c "$*" >> "$LOG" 2>&1; rc=$?
  if [ "$rc" = 0 ]; then RESULTS+=("PASS  $name"); echo "PASS  $name"
  else RESULTS+=("FAIL  $name (کد $rc)"); echo "FAIL  $name (کد $rc)"; FAILED=$((FAILED + 1)); fi
}
# مثل step ولی شکستش بقیهٔ مراحل را بی‌معنی می‌کند
must() { step "$@"; [ "$FAILED" = 0 ] || { echo "مرحلهٔ ضروری شکست خورد؛ ادامه بی‌معنی است. گزارش: $LOG"; summary; exit 1; }; }
summary() { echo; echo "──────── خلاصه ────────"; printf '%s\n' "${RESULTS[@]}" | tee -a "$LOG"; echo "گزارش کامل: $LOG"; }
# اجرای دستور به‌عنوان کاربر سرویس با محیط برنامه (متغیرهای محرمانه فقط از فایل خوانده می‌شوند، نه از آرگومان)
as_ravin() { sudo -u ravin bash -c "set -a; . $ENVF; set +a; cd $APP; $*"; }
# اجرای کد پایتون از stdin در shell جنگو (کد و مقادیر تصادفی داخل پایتون ساخته می‌شوند؛ هیچ رمزی در آرگومان پروسه نیست)
dj_shell() { sudo -u ravin bash -c "set -a; . $ENVF; set +a; cd $APP; $PY manage.py shell"; }
export -f as_ravin dj_shell
export ENVF APP PY

# ── ۱. پیش‌نیازها ─────────────────────────────────────────────────────────────
must "نصب بسته‌ها" "export DEBIAN_FRONTEND=noninteractive; for i in \$(seq 60); do fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock >/dev/null 2>&1 || break; sleep 5; done; apt-get -o DPkg::Lock::Timeout=300 update -qq && apt-get -o DPkg::Lock::Timeout=300 install -y -qq python3 python3-venv python3-dev build-essential libpq-dev libjpeg-dev zlib1g-dev postgresql nginx git certbot python3-certbot-nginx ufw curl rsync openssl"
# پس از نصب، دوباره بررسی می‌کنیم PostgreSQL تازه (نصب‌شده توسط خودمان) دیتابیس ravin نداشته باشد
fresh_host_check

# ── ۲. کاربر و پوشه‌ها (دقیقاً مطابق deployment.md §۲) ────────────────────────
must "کاربر و پوشه‌ها" "
  id ravin >/dev/null 2>&1 || adduser --system --group --home /srv/ravin ravin
  usermod -aG ravin www-data
  mkdir -p /srv/ravin/{app,media,shop_private,plugins_installed,plugin_backups,update_work,update_backups} /etc/ravin /var/backups/ravin /var/cache/ravin
  chown ravin:ravin /srv/ravin/plugins_installed /srv/ravin/plugin_backups && chmod 750 /srv/ravin/plugins_installed && chmod 700 /srv/ravin/plugin_backups
  chown ravin:www-data /srv/ravin/media /var/cache/ravin && chmod 2750 /srv/ravin/media && chmod 750 /var/cache/ravin
  chown ravin:ravin /var/backups/ravin && chmod 700 /var/backups/ravin
  touch $SENTINEL && chmod 600 $SENTINEL"

# ── ۳. کد و وابستگی‌ها ────────────────────────────────────────────────────────
# بدون --delete: هرگز چیزی را که در مقصد هست پاک نمی‌کند. excludeها به ریشهٔ پروژه قفل شده‌اند.
must "کپی کد" "rsync -a --exclude /.venv --exclude /db.sqlite3 --exclude /media --exclude /staticfiles --exclude __pycache__ '$SRC/' '$APP/' && chown -R ravin:ravin '$APP' && mkdir -p '$APP/media'"
must "venv و requirements.lock" "sudo -u ravin python3 -m venv /srv/ravin/venv && sudo -u ravin /srv/ravin/venv/bin/pip install -q -r $APP/requirements.lock"
step "plugins/ فقط‌خواندنی برای ravin" "chown -R root:ravin $APP/plugins && chmod -R g-w,o-rwx $APP/plugins && ! sudo -u ravin test -w $APP/plugins"

# ── ۴. PostgreSQL ─────────────────────────────────────────────────────────────
DBPW="$(openssl rand -hex 16)"
SECRET="$(openssl rand -base64 48 | tr -d '\n=+/')"
SKEY="$($PY -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || true)"
[ -n "$SKEY" ] || echo "هشدار: ساخت RAVIN_SECRETS_KEY ناموفق بود؛ بدون آن اسرار درگاه ذخیره نمی‌شود" | tee -a "$LOG"
pg_setup() {  # رمز فقط از stdin (heredoc) می‌رود، نه آرگومان
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL || return 1
DO \$\$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ravin') THEN
    ALTER ROLE ravin LOGIN PASSWORD '$DBPW';
  ELSE
    CREATE ROLE ravin LOGIN PASSWORD '$DBPW';
  END IF;
END \$\$;
SQL
  sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='ravin'" | grep -q 1 \
    || sudo -u postgres createdb -O ravin -E UTF8 ravin
}
export -f pg_setup; export DBPW
must "PostgreSQL: کاربر و دیتابیس" pg_setup

# ── ۵. متغیرهای محیطی ─────────────────────────────────────────────────────────
if [ -n "$DOMAIN" ]; then
  HOSTS="$DOMAIN"; ORIGINS="https://$DOMAIN"; SITE="https://$DOMAIN"; SSLR=true
else
  HOSTS="localhost,127.0.0.1"; ORIGINS="http://localhost"; SITE="http://localhost"; SSLR=false
fi
write_env() {  # مقادیر از heredoc می‌آیند (نه آرگومان)؛ در .env.example مقادیر پیش‌فرض قبلی با این‌ها بازنویسی می‌شوند (آخرین مقدار برنده است)
  install -m 640 -o root -g ravin "$APP/.env.example" "$ENVF" || return 1
  cat >> "$ENVF" <<EOF

# ---- افزوده‌شده توسط scripts/vps_smoke.sh ----
DJANGO_SETTINGS_MODULE=config.settings.production
DJANGO_SECRET_KEY=$SECRET
DJANGO_ALLOWED_HOSTS=$HOSTS
DJANGO_CSRF_TRUSTED_ORIGINS=$ORIGINS
DATABASE_URL=postgres://ravin:$DBPW@127.0.0.1:5432/ravin
RAVIN_SITE_URL=$SITE
DJANGO_SECURE_SSL_REDIRECT=$SSLR
${SKEY:+RAVIN_SECRETS_KEY=$SKEY}
EOF
  chmod 640 "$ENVF"; chown root:ravin "$ENVF"
}
export -f write_env; export SECRET HOSTS ORIGINS SITE SSLR SKEY
must "نوشتن $ENVF" write_env

# ── ۶. مهاجرت، استاتیک، مدیرکل، preflight ─────────────────────────────────────
must "migrate" "as_ravin '$PY manage.py migrate --noinput'"
must "collectstatic" "as_ravin '$PY manage.py collectstatic --noinput'"
step "ساخت مدیرکل آزمایشی (رمز تصادفی، چاپ نمی‌شود)" "dj_shell <<'PY'
import secrets
from django.contrib.auth import get_user_model
get_user_model().objects.create_superuser('smoke', 'smoke@example.invalid', secrets.token_urlsafe(24))
PY"
echo "=== ravin_preflight ===" >> "$LOG"
as_ravin "$PY manage.py ravin_preflight" >> "$LOG" 2>&1; PF=$?
if [ -n "$DOMAIN" ]; then
  if [ "$PF" = 0 ]; then RESULTS+=("PASS  ravin_preflight (بدون خطا)"); echo "PASS  ravin_preflight"
  else RESULTS+=("FAIL  ravin_preflight (کد $PF؛ جزئیات در لاگ)"); echo "FAIL  ravin_preflight"; FAILED=$((FAILED + 1)); fi
else
  RESULTS+=("INFO  ravin_preflight کد $PF — در حالت محلی خطای HTTPS/HSTS/کوکی‌ها مورد انتظار است؛ خروجی در لاگ"); echo "INFO  ravin_preflight کد $PF (حالت محلی؛ لاگ را ببینید)"
fi

# ── ۷. systemd + Gunicorn ─────────────────────────────────────────────────────
must "نصب unitهای systemd" "cp $APP/deploy/ravin.service /etc/systemd/system/ && cp $APP/deploy/ravin-expire-orders.service $APP/deploy/ravin-expire-orders.timer /etc/systemd/system/ && systemctl daemon-reload"
must "systemctl enable --now ravin" "systemctl enable --now ravin && sleep 4 && systemctl is-active --quiet ravin"
step "تایمر ravin-expire-orders فعال" "systemctl enable --now ravin-expire-orders.timer && systemctl is-active --quiet ravin-expire-orders.timer"
step "سوکت Gunicorn" "ls -l /run/ravin/ && curl -sf --unix-socket /run/ravin/gunicorn.sock -H 'Host: $HOST_HDR' -H 'X-Forwarded-Proto: https' http://localhost/healthz | grep -q ok"

# ── ۸. Nginx ──────────────────────────────────────────────────────────────────
# فقط سایت default برداشته می‌شود (بررسی «تازه‌بودن» تضمین کرده سایت دیگری نیست)
must "تنظیم Nginx" "
  cp $APP/deploy/ravin-proxy.conf /etc/nginx/ravin-proxy.conf &&
  cp $APP/deploy/nginx-ravin.conf /etc/nginx/sites-available/ravin &&
  sed -i 's/example.com/${DOMAIN:-localhost}/g' /etc/nginx/sites-available/ravin &&
  ln -sf /etc/nginx/sites-available/ravin /etc/nginx/sites-enabled/ravin && rm -f /etc/nginx/sites-enabled/default &&
  nginx -t && systemctl reload nginx"
step "healthz از طریق Nginx (پورت ۸۰)" "curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: $HOST_HDR' http://127.0.0.1/healthz | tee /dev/stderr | grep -qE '^(200|301|308)$'"
step "استاتیک از Nginx/WhiteNoise" "f=\$(find $APP/staticfiles -name '*.css' | head -1); [ -n \"\$f\" ] && curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: $HOST_HDR' -H 'X-Forwarded-Proto: https' http://127.0.0.1/static/\${f#$APP/staticfiles/} | grep -q 200"
step "media از Nginx" "echo smoke > /srv/ravin/media/smoke.txt && chown ravin:www-data /srv/ravin/media/smoke.txt && curl -s -H 'Host: $HOST_HDR' http://127.0.0.1/media/smoke.txt | grep -q smoke"

# ── ۹. HTTPS (فقط حالت کامل) ──────────────────────────────────────────────────
if [ -n "$DOMAIN" ]; then
  STG=""; [ "${CERTBOT_STAGING:-}" = 1 ] && STG="--staging"
  if [ "${SMOKE_UFW:-}" = 1 ]; then
    # پورت واقعی sshd هم باز می‌شود تا قفل شدن از سرور رخ ندهد
    step "ufw: SSH (پورت واقعی sshd) و Nginx Full" "for p in \$( (sshd -T 2>/dev/null | awk '/^port /{print \$2}'; echo 22) | sort -u ); do ufw allow \"\$p/tcp\"; done; ufw allow 'Nginx Full' && ufw --force enable"
  else
    RESULTS+=("SKIP  ufw (برای فعال‌کردن: SMOKE_UFW=1؛ فایروال ارائه‌دهنده باید پورت ۸۰ و ۴۴۳ را باز بگذارد)"); echo "SKIP  ufw"
  fi
  step "certbot --nginx" "certbot --nginx $STG -d $DOMAIN --non-interactive --agree-tos -m '$CERTBOT_EMAIL' --redirect"
  step "certbot renew --dry-run" "certbot renew --dry-run"
  step "HTTPS healthz" "curl -s ${STG:+-k} https://$DOMAIN/healthz | grep -q ok"
  step "هدر HSTS و CSP" "h=\$(curl -sI ${STG:+-k} https://$DOMAIN/); echo \"\$h\" | grep -qi strict-transport-security && echo \"\$h\" | grep -qi content-security-policy"
else
  RESULTS+=("SKIP  certbot/HTTPS/ufw (حالت محلی)"); echo "SKIP  certbot/HTTPS/ufw (حالت محلی)"
fi

# ── ۱۰. پشتیبان و بازیابی واقعی ───────────────────────────────────────────────
step "ساخت دادهٔ نشانه (کاربر + فایل)" "dj_shell <<'PY' && echo keep-me > /srv/ravin/media/marker.txt && chown ravin:www-data /srv/ravin/media/marker.txt
import secrets
from django.contrib.auth import get_user_model
get_user_model().objects.create_user('marker', 'marker@example.invalid', secrets.token_urlsafe(24))
PY"
step "backup.sh" "sudo -u ravin $APP/deploy/backup.sh && ls /var/backups/ravin/*.dump >/dev/null && ls /var/backups/ravin/*.sha256 >/dev/null"
BK="$(ls -1t /var/backups/ravin/ravin-*.dump 2>/dev/null | head -1)"
if [ -n "$BK" ]; then
  B="${BK%.dump}"; STAMP="$(basename "$B")"
  step "حذف دادهٔ نشانه (شبیه‌سازی فاجعه)" "systemctl stop ravin && dj_shell <<'PY' && rm -f /srv/ravin/media/marker.txt
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username='marker').delete()
PY"
  # پشتیبان دست‌کاری‌شده: کپی کل مجموعه (با همان نام‌ها که .sha256 به آن‌ها اشاره دارد) در پوشهٔ موقتِ خودمان، یک بایت اضافه به dump
  step "رد پشتیبان دست‌کاری‌شده (باید دقیقاً کد ۲ بدهد و دیتابیس را دست نزند)" "
    T=/var/backups/ravin/.smoke-tamper && rm -rf \$T && mkdir -m 700 \$T && chown ravin:ravin \$T &&
    cp $B.* \$T/ && printf x >> \$T/$STAMP.dump && chown -R ravin:ravin \$T &&
    sudo -u ravin env RAVIN_RESTORE_YES=1 $APP/deploy/restore.sh \$T/$STAMP.dump; rc=\$?;
    rm -rf \$T; [ \$rc = 2 ] || { echo \"کد خروج \$rc (انتظار ۲)\"; exit 1; };
    dj_shell <<'PY'
from django.contrib.auth import get_user_model
assert not get_user_model().objects.filter(username='marker').exists(), 'بازیابی دست‌کاری‌شده اجرا شده است!'
PY"
  step "restore.sh" "sudo -u ravin env RAVIN_RESTORE_YES=1 $APP/deploy/restore.sh $B.dump $B.media.tar.gz"
  step "migrate پس از بازیابی" "as_ravin '$PY manage.py migrate --noinput'"
  step "دادهٔ نشانه بازگشته" "dj_shell <<'PY' && grep -q keep-me /srv/ravin/media/marker.txt
from django.contrib.auth import get_user_model
assert get_user_model().objects.filter(username='marker').exists()
PY"
  step "سرویس پس از بازیابی" "systemctl start ravin && sleep 4 && systemctl is-active --quiet ravin && curl -sf --unix-socket /run/ravin/gunicorn.sock -H 'Host: $HOST_HDR' -H 'X-Forwarded-Proto: https' http://localhost/healthz | grep -q ok"
else
  RESULTS+=("FAIL  فایل پشتیبان پیدا نشد؛ مراحل بازیابی اجرا نشد"); FAILED=$((FAILED + 1))
fi

# ── ۱۱. راه‌اندازی دوبارهٔ سرویس ──────────────────────────────────────────────
step "restart ravin" "systemctl restart ravin && sleep 3 && systemctl is-active --quiet ravin"

# ── ۱۲. پاک‌سازی دادهٔ آزمایشی (فقط چیزهایی که خود اسکریپت با نام مشخص ساخته) ──
step "پاک‌سازی دادهٔ آزمایشی (کاربران smoke/marker، smoke.txt/marker.txt)" "dj_shell <<'PY' && rm -f /srv/ravin/media/smoke.txt /srv/ravin/media/marker.txt
from django.contrib.auth import get_user_model
get_user_model().objects.filter(username__in=['smoke', 'marker'], email__endswith='@example.invalid').delete()
PY"

summary
echo "نسخهٔ پروژه: $(grep -m1 -E 'VERSION' "$SRC/apps/core/version.py" 2>/dev/null) | PostgreSQL: $(sudo -u postgres psql -tAc 'show server_version' 2>/dev/null) | $(nginx -v 2>&1) | $($PY -V 2>&1)" | tee -a "$LOG"
echo "پشتیبان‌های ساخته‌شده در /var/backups/ravin می‌مانند (حاوی همان دادهٔ آزمایشی‌اند)."
[ "$FAILED" = 0 ] && echo "همه مراحل موفق بود" || echo "$FAILED مرحله ناموفق بود؛ فایل $LOG را بفرستید"
exit "$FAILED"
