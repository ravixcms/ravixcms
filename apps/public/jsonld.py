"""schema.org structured data (JSON-LD) for the public pages.

The nodes are plain dicts; they are put in one ``@graph`` and written into the page head by ``theme_head`` through ``json.dumps`` with
``<``, ``>`` and ``&`` escaped, so no title or name can close the script tag. Every node that other nodes refer to has a stable ``@id``
built from the site address (``<origin>/#organization``, ``<origin>/#website``, ``<page-url>#webpage`` …), so search engines see the same
entity on every page and in every language. Plugins can change the nodes through the ``public.jsonld`` filter hook.
"""
from django.conf import settings
from django.utils import translation
from django.utils.encoding import iri_to_uri

from apps.core.identity import is_https_profile
from apps.plugins.hooks import apply_filter

from .seo import absolute_url, clip, hreflang

CONTEXT = "https://schema.org"


def origin(request) -> str:
    return (settings.SITE_URL or f"{request.scheme}://{request.get_host()}").rstrip("/")


def organization_id(request, cfg) -> str:
    return f"{origin(request)}/#{'person' if cfg.identity_type == 'person' else 'organization'}"


def website_id(request) -> str:
    return f"{origin(request)}/#website"


def _language() -> str:
    code = (translation.get_language() or "").lower()
    return hreflang(code) if code else ""


def _logo_url(request, cfg, site) -> str:
    media = cfg.identity_logo
    url = media.file.url if media is not None and media.file else site.get("logo_url", "")
    return absolute_url(request, url) if url else ""


def identity_node(request, site, cfg, *, full: bool) -> dict:
    """The Organization (or Person) behind the site. ``full`` on the home page; elsewhere only the short form used as publisher."""
    person = cfg.identity_type == "person"
    node = {"@type": "Person" if person else "Organization", "@id": organization_id(request, cfg), "name": cfg.identity_name or site["name"]}
    logo = _logo_url(request, cfg, site)
    if logo:
        node["image" if person else "logo"] = logo
    if not full:
        return node
    node["url"] = origin(request) + "/"
    names = [n for n in (cfg.identity_alternate_names or []) if isinstance(n, str) and n and n != node["name"]]
    if names:
        node["alternateName"] = names
    description = cfg.identity_description or site.get("description", "")
    if description:
        node["description"] = clip(description, 300)
    same_as = [u for u in (cfg.identity_same_as or []) if is_https_profile(u)]  # re-checked: the stored list may predate the check
    if same_as:
        node["sameAs"] = same_as
    return node


def website_node(request, site, cfg) -> dict:
    node = {"@type": "WebSite", "@id": website_id(request), "url": absolute_url(request, site["home_url"]), "name": site["name"],
            "publisher": {"@id": organization_id(request, cfg)}}
    if _language():
        node["inLanguage"] = _language()
    if site.get("search_url"):
        node["potentialAction"] = {"@type": "SearchAction", "target": {"@type": "EntryPoint", "urlTemplate": absolute_url(request, site["search_url"]) + "?q={search_term_string}"},
                                   "query-input": "required name=search_term_string"}
    return node


def breadcrumb_node(request, crumbs, page_url: str):
    """BreadcrumbList for ``crumbs`` ((label, url) pairs or objects with ``label``/``url``); the last item is the page itself. None below two items."""
    items = [(getattr(c, "label", None) or c[0], getattr(c, "url", None) if hasattr(c, "url") else c[1]) for c in crumbs]
    if len(items) < 2:
        return None
    elements = []
    for position, (label, url) in enumerate(items, start=1):
        element = {"@type": "ListItem", "position": position, "name": str(label)}
        target = absolute_url(request, url) if url else (page_url if position == len(items) else "")
        if target:
            element["item"] = target
        elements.append(element)
    return {"@type": "BreadcrumbList", "@id": page_url + "#breadcrumb", "itemListElement": elements}


def home_nodes(request, site, cfg) -> list:
    return [identity_node(request, site, cfg, full=True), website_node(request, site, cfg)]


def post_nodes(request, site, cfg, post, item, *, crumbs, translations=()) -> list:
    """BlogPosting (+ BreadcrumbList). ``translations``: ``[(hreflang, absolute_url), …]`` of the other language versions of this post."""
    url = absolute_url(request, item.url)
    node = {
        "@type": "BlogPosting", "@id": url + "#article", "headline": post.title[:110], "url": url,
        "datePublished": item.date_iso, "dateModified": post.updated_at.isoformat() if post.updated_at else item.date_iso,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "isPartOf": {"@id": website_id(request)},
        "publisher": identity_node(request, site, cfg, full=False),
    }
    language = hreflang(post.language) if post.language else _language()
    if language:
        node["inLanguage"] = language
    description = clip(post.seo_description or item.excerpt)
    if description:
        node["description"] = description
    if item.author_name:
        node["author"] = {"@type": "Person", "name": item.author_name}
    if item.image_url:
        node["image"] = [absolute_url(request, item.image_url)]
    others = [{"@type": "BlogPosting", "@id": link + "#article", "url": link, "inLanguage": lang} for lang, link in translations if lang != "x-default" and link != url]
    if others:
        node["workTranslation"] = others
    nodes = [node]
    crumb = breadcrumb_node(request, crumbs, url)
    if crumb is not None:
        node["breadcrumb"] = {"@id": crumb["@id"]}
        nodes.append(crumb)
    return nodes


def page_nodes(request, site, cfg, page, item, *, crumbs, translations=()) -> list:
    url = absolute_url(request, item.url)
    node = {"@type": "WebPage", "@id": url + "#webpage", "url": url, "name": page.title, "isPartOf": {"@id": website_id(request)}}
    language = hreflang(page.language) if page.language else _language()
    if language:
        node["inLanguage"] = language
    description = clip(page.seo_description or item.excerpt)
    if description:
        node["description"] = description
    if item.date_iso:
        node["datePublished"] = item.date_iso
    if page.updated_at:
        node["dateModified"] = page.updated_at.isoformat()
    others = [{"@type": "WebPage", "@id": link + "#webpage", "url": link, "inLanguage": lang} for lang, link in translations if lang != "x-default" and link != url]
    if others:
        node["workTranslation"] = others
    nodes = [node]
    crumb = breadcrumb_node(request, crumbs, url)
    if crumb is not None:
        node["breadcrumb"] = {"@id": crumb["@id"]}
        nodes.append(crumb)
    return nodes


def build(request, site, nodes, *, kind: str, obj_id=None, robots: str = "") -> list:
    """``[{"@context": …, "@graph": nodes}]`` for ``make_seo(jsonld=…)``, after the ``public.jsonld`` hook. Pages that ask search engines not
    to index them get none."""
    if "noindex" in robots or not nodes:
        return []
    nodes = apply_filter("public.jsonld", list(nodes), kind=kind, obj_id=obj_id, site=site, request=request)
    return [{"@context": CONTEXT, "@graph": nodes}] if nodes else []


__all__ = ["build", "home_nodes", "post_nodes", "page_nodes", "identity_node", "website_node", "breadcrumb_node", "organization_id", "website_id", "origin", "iri_to_uri"]
