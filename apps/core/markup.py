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
# Every text block gets its own direction from its first strong character (dir="auto"): a Persian paragraph in an English page
# (or the reverse) reads and aligns correctly, and a mixed post keeps working paragraph by paragraph. Code stays left-to-right (CSS).
AUTO_DIR_TAGS = ("p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "table")


def render_markdown(text: str, *, auto_dir: bool = True) -> str:
    if not text:
        return ""
    html = markdown.markdown(text, extensions=["extra", "sane_lists", "nl2br"], output_format="html")
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        set_tag_attribute_values={tag: {"dir": "auto"} for tag in AUTO_DIR_TAGS} if auto_dir else {},
    )
