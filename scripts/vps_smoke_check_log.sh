#!/usr/bin/env bash
# بررسی اینکه مقدار هیچ‌یک از رازهای نصب (SECRET_KEY، RAVIX_SECRETS_KEY، رمز دیتابیس) در لاگ آزمون دودی نیامده — پیش از فرستادن لاگ اجرا کنید.
# اجرا: sudo bash scripts/vps_smoke_check_log.sh [/etc/ravix/ravix.env] [/root/ravix-vps-smoke.log]
# خروجی: «OK: ...» با کد ۰، یا «LEAK: ...» با کد ۱ (در این صورت لاگ را نفرستید و به من خبر دهید). مقدار رازها را چاپ نمی‌کند.
set -u
ENVF="${1:-/etc/ravix/ravix.env}"; LOGF="${2:-/root/ravix-vps-smoke.log}"; leak=0
if [ ! -r "$ENVF" ] || [ ! -r "$LOGF" ]; then echo "فایل env یا لاگ خوانا نیست"; exit 2; fi
for k in DJANGO_SECRET_KEY RAVIX_SECRETS_KEY; do
  v="$(grep "^$k=" "$ENVF" | tail -1 | cut -d= -f2-)"
  if [ -n "$v" ] && grep -qF -- "$v" "$LOGF"; then echo "LEAK: $k"; leak=1; fi
done
p="$(grep '^DATABASE_URL=' "$ENVF" | tail -1 | sed -E 's#^[^:]*://[^:]*:([^@]*)@.*#\1#')"
if [ -n "$p" ] && grep -qF -- "$p" "$LOGF"; then echo "LEAK: db password"; leak=1; fi
if [ "$leak" = 0 ]; then echo "OK: مقدار هیچ رازی در لاگ پیدا نشد"; fi
exit "$leak"
