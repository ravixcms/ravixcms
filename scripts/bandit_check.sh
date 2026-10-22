#!/usr/bin/env bash
# bandit با baseline: موارد شناخته‌شده در .bandit-baseline.json ثبت‌اند و فقط موارد «تازه» (Medium به بالا) شکست می‌دهند.
# به‌روزرسانی baseline پس از بازبینی دستی:  scripts/bandit_check.sh --update   (و commit کردن فایل)
set -euo pipefail
cd "$(dirname "$0")/.."
EXCLUDE='*/tests/*,*/migrations/*'
if [ "${1:-}" = "--update" ]; then
  bandit -q -r apps config -x "$EXCLUDE" -f json -o .bandit-baseline.json || true
  echo "baseline به‌روز شد: $(python3 -c 'import json;print(len(json.load(open(".bandit-baseline.json"))["results"]))') مورد"
  exit 0
fi
bandit -q -r apps config -x "$EXCLUDE" -b .bandit-baseline.json -ll
