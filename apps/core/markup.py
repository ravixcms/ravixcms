"""رندر امن Markdown: تبدیل به HTML و پاک‌سازی کامل خروجی (جلوگیری از XSS)."""
import html as html_lib
import re
from urllib.parse import urlsplit

import markdown
import nh3
from django.conf import settings

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


LINK_REL = "noopener noreferrer nofollow"
INTERNAL_LINK_REL = "noopener noreferrer"  # a link to the site itself is not "nofollow"; the rest of the protection stays
_A_TAG = re.compile(r"""<a\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
_HREF = re.compile(r'\shref="([^"]*)"')


def _bare_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def own_hosts() -> set[str]:
    """Host names that mean "this site": the host of SITE_URL and the ALLOWED_HOSTS entries without wildcards, each without ``www.``."""
    hosts = set()
    site_host = urlsplit(getattr(settings, "SITE_URL", "") or "").hostname
    if site_host:
        hosts.add(_bare_host(site_host))
    for entry in getattr(settings, "ALLOWED_HOSTS", []) or []:
        if entry and not str(entry).startswith((".", "*")):
            hosts.add(_bare_host(str(entry).split(":")[0]))
    hosts.discard("")
    return hosts


def is_internal_href(href: str, hosts: set[str] | None = None) -> bool:
    """A link that stays on this site: relative (``/x``, ``#y``, ``x/y``, ``?q``) or an http(s) address whose host is one of the site's own."""
    href = (href or "").strip()
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    if parts.scheme in ("http", "https") or (not parts.scheme and href.startswith("//")):
        return _bare_host(parts.hostname or "") in (own_hosts() if hosts is None else hosts) and bool(parts.hostname)
    return not parts.scheme and not parts.netloc


def _relax_internal_links(cleaned: str) -> str:
    """nh3 puts ``rel="noopener noreferrer nofollow"`` on every link; a link to this very site only loses ``nofollow``. Runs on sanitized output."""
    if 'rel="' + LINK_REL + '"' not in cleaned:
        return cleaned
    hosts = own_hosts()

    def fix(match):
        tag = match.group(0)
        href = _HREF.search(tag)
        if href and is_internal_href(html_lib.unescape(href.group(1)), hosts):
            return tag.replace(f'rel="{LINK_REL}"', f'rel="{INTERNAL_LINK_REL}"')
        return tag

    return _A_TAG.sub(fix, cleaned)


def render_markdown(text: str, *, auto_dir: bool = True) -> str:
    if not text:
        return ""
    html = markdown.markdown(text, extensions=["extra", "sane_lists", "nl2br"], output_format="html")
    return _relax_internal_links(_sanitize(html, auto_dir))


def _sanitize(html: str, auto_dir: bool) -> str:
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel=LINK_REL,
        set_tag_attribute_values={tag: {"dir": "auto"} for tag in AUTO_DIR_TAGS} if auto_dir else {},
    )
