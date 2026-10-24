# مشارکت
مخزن: <https://github.com/ravixcms/ravixcms> · وب‌سایت: <https://ravixcms.com> · مستندات: <https://ravixcms.com/docs>. گزارش خطا و پیشنهاد: [Issues](https://github.com/ravixcms/ravixcms/issues)؛ آسیب‌پذیری امنیتی را عمومی گزارش نکنید؛ به security@ravixcms.com بفرستید ([`SECURITY.md`](SECURITY.md)). پشتیبانی: support@ravixcms.com.

0. مخزن را Fork کنید: `git clone https://github.com/<حساب-شما>/ravixcms.git`
1. `python -m venv .venv && .venv/bin/pip install -r requirements.lock`
2. تست‌ها: `.venv/bin/python manage.py test --parallel 4` (برای اجرای موازی `pip install tblib` را هم بزنید؛ بدونش وقتی تستی شکست بخورد جنگو فقط «cannot pickle 'traceback' object» می‌دهد و شکست واقعی پنهان می‌ماند) و با فروشگاه خاموش: `RAVIX_SHOP_ENABLED=false .venv/bin/python manage.py test` (ترتیبی).
3. تغییر مدل ⇒ مهاجرت: `manage.py makemigrations --check --dry-run` باید پاک باشد.
4. هیچ رمز/کلید/داده‌ٔ شخصی را کامیت نکنید (`.env`, `db.sqlite3`, `media/`, `keys/` در `.gitignore` هستند).
5. تغییر مهم را در `CHANGELOG.md` بنویسید. فرایند انتشار: `docs/release.md`.
