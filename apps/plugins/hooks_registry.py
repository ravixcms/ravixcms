"""فهرست hookهای رسمی هسته. افزونه فقط می‌تواند روی نام‌های این فهرست ثبت‌نام کند (در manifest)."""

# نام ← نوع. filter: مقدار را می‌گیرد و مقدار (تغییرداده‌شده) برمی‌گرداند. action: فقط اجرا می‌شود.
HOOKS = {
    "public.seo": "filter",         # (seo: dict, api, kind, obj_id, site, request) -> dict
    "shop.gateways": "filter",      # (gateways: dict[str, type[BaseGateway]], api) -> dict — افزودن درگاه پرداخت (فقط افزونهٔ فعال و تأییدشده)
    "shop.order_status_changed": "action",  # (api, order_id, number, old_status, new_status, phone, language, text) — after an order status change; the SMS hook
    "comments.before_save": "filter",   # (data: dict, api, post_id, request) -> dict — فقط کلید status (و فقط pending/spam) پذیرفته می‌شود
    "comments.created": "action",       # (api, comment_id, post_id, status, request) — پس از ثبت دیدگاه عمومی
    "comments.status_changed": "action",  # (api, comment_id, old, new, request) — پس از تغییر وضعیت توسط مدیر
    "content.saved": "action",      # (api, kind, obj_id, request)  — پس از ذخیرهٔ نوشته/صفحه
}
