#!/usr/bin/env bash
# ساخت فایل .env برای docker compose با رمزهای تصادفی.  استفاده:  ./docker/init-env.sh [دامنه] [--tls]
#   ./docker/init-env.sh                       # localhost روی HTTP (برای آزمایش)
#   ./docker/init-env.sh example.com --tls     # دامنه با HTTPS (docker-compose.tls.yml)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ -e .env ] && { echo ".env از قبل هست؛ برای جلوگیری از نابودی رمزها دست نمی‌زنم (حذفش کنید یا دستی ویرایش کنید)." >&2; exit 1; }
domain="localhost"; tls=0
for a in "$@"; do case "$a" in --tls) tls=1;; -*) echo "گزینهٔ ناشناخته: $a" >&2; exit 2;; *) domain="$a";; esac; done
case "$domain" in *[!A-Za-z0-9.-]*|"") echo "دامنه نامعتبر است: $domain" >&2; exit 2;; esac
rand_hex() { python3 -c 'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))' "$1" 2>/dev/null || openssl rand -hex "$1"; }
rand_url() { python3 -c 'import secrets; print(secrets.token_urlsafe(64))' 2>/dev/null || openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n'; }
scheme=http; [ "$tls" = 1 ] && scheme=https
umask 077
cat > .env <<ENV
# تولیدشده توسط docker/init-env.sh — در مخزن کد نگذارید. از این فایل پشتیبان جداگانه (رمزنگاری‌شده) بگیرید.
RAVIX_DOMAIN=$domain
RAVIX_PUBLIC_URL=$scheme://$domain
POSTGRES_PASSWORD=$(rand_hex 24)
DJANGO_SECRET_KEY=$(rand_url)
RAVIX_INSTALL_TOKEN=$(rand_hex 16)
RAVIX_HTTP_PORT=80
RAVIX_HTTPS_PORT=443
DJANGO_SECURE_SSL_REDIRECT=$([ "$tls" = 1 ] && echo true || echo false)
# ایمیل (SMTP واقعی):
# EMAIL_HOST=
# EMAIL_HOST_USER=
# EMAIL_HOST_PASSWORD=
ENV
echo ".env ساخته شد. توکن نصب: $(grep ^RAVIX_INSTALL_TOKEN= .env | cut -d= -f2)"
echo "بعد:  docker compose$([ "$tls" = 1 ] && echo ' -f docker-compose.yml -f docker-compose.tls.yml') up -d --build   و   $scheme://$domain/install/"
