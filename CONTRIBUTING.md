# مشارکت
1. `python -m venv .venv && .venv/bin/pip install -r requirements.lock`
2. تست‌ها: `.venv/bin/python manage.py test --parallel 4` و با فروشگاه خاموش: `RAVIX_SHOP_ENABLED=false .venv/bin/python manage.py test` (ترتیبی).
3. تغییر مدل ⇒ مهاجرت: `manage.py makemigrations --check --dry-run` باید پاک باشد.
4. هیچ رمز/کلید/داده‌ٔ شخصی را کامیت نکنید (`.env`, `db.sqlite3`, `media/`, `keys/` در `.gitignore` هستند).
5. تغییر مهم را در `CHANGELOG.md` بنویسید. فرایند انتشار: `docs/release.md`.
