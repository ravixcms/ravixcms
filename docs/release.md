# فرایند انتشار نسخه‌دار (GitHub)

> گردش‌کارهای `.github/workflows/*.yml` روی GitHub واقعی اجرا **نشده‌اند**؛ مراحل ساخت/بررسی بسته در یک شبیه‌سازی محلی (همان دستورها، بدون GitHub) آزموده شد. اولین اجرا را روی یک تگ آزمایشی (`v0.0.0-test`) امتحان کنید.

1. `apps/core/version.py` → `RAVIX_VERSION` را بالا ببرید و `CHANGELOG.md` را تکمیل کنید.
2. تست‌ها و `python scripts/e2e_updater.py` / `scripts/e2e_backup_restore.py` را اجرا کنید.
3. کامیت و تگ: `git tag -a vX.Y.Z -m "Ravix X.Y.Z" && git push origin vX.Y.Z`.
4. `release.yml` خودکار: تطبیق تگ با `RAVIX_VERSION`، اجرای تست‌ها، ساخت `ravix-X.Y.Z.zip` (با `TZ=UTC` برای هش یکسان) + `SHA256SUMS`، راستی‌آزمایی با `sha256sum -c` و `scripts/verify_release.py`، ساخت Release در GitHub.
5. (اختیاری) بستهٔ امضاشدهٔ به‌روزرسانی: `ravix_release core --out dist/ --key-file <کلید خصوصی>` روی ماشین امن ناشر؛ کلید خصوصی را هرگز در مخزن یا Secrets عمومی نگذارید مگر آگاهانه.
6. بررسی وابستگی‌ها: `pip-audit -r requirements.lock` (CI هفتگی هم اجرا می‌کند).

## بستهٔ کاربر نهایی ≠ بستهٔ توسعه
- `git archive` فقط فایل‌های ردیابی‌شدهٔ git را می‌گیرد (پس `.env`، `db.sqlite3`، `media/`، `.venv`، کلیدها نمی‌آیند)؛ `.gitattributes` آزمون‌ها، `.github` و اسکریپت‌های آزمایش/انتشار را هم حذف می‌کند.
- `TZ=UTC` لازم است: زمان فایل‌های zip از منطقهٔ زمانی سازنده می‌آید و بدون آن هش بین دو ساخت فرق می‌کند. با آن، ساخت از یک commit در هر منطقهٔ زمانی همان SHA-256 را می‌دهد.

## راستی‌آزمایی توسط کاربر
```
sha256sum -c SHA256SUMS      # باید «ravix-X.Y.Z.zip: OK» بدهد
```
`SHA256SUMS` فقط یکپارچگی دانلود را تضمین می‌کند؛ امضای ناشر مرحلهٔ بعدی است.
