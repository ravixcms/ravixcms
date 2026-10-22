"""رندر امن Markdown: تبدیل به HTML و پاک‌سازی کامل خروجی (جلوگیری از XSS)."""
import markdown
import nh3

ALLOWED_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "u", "s", "del", "code", "pre",
    "blockquote", "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tr", "th", "td", "span", "div",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align"},
    "td": {"align"},
}
ALLOWED_SCHEMES = {"http", "https", "mailto"}


def render_markdown(text: str) -> str:
    if not text:
        return ""
    html = markdown.markdown(text, extensions=["extra", "sane_lists", "nl2br"], output_format="html")
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
