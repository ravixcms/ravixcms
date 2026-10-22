"""نقطه‌ی اتصال صفحه‌ساز به سایت عمومی: نسخه‌ی منتشرشده‌ی یک صفحه را (در صورت وجود) به HTML امن تبدیل می‌کند."""
from .models import PageDesign
from .render import render_document


def published_body(page):
    """(html, show_title) یا None اگر صفحه طرح منتشرشده ندارد (آن‌وقت همان محتوای Markdown نمایش داده می‌شود)."""
    design = PageDesign.objects.filter(page=page, published_version__isnull=False).select_related("published_version").first()
    if design is None:
        return None
    doc = design.published_version.document
    html = render_document(doc)
    if not html:
        return None  # سند خراب → بازگشت امن به محتوای متنی
    return html, bool(doc.get("meta", {}).get("show_title", True))
