"""schema.org JSON-LD: valid and safe output, stable @ids, identity settings, drafts/noindex, translations, plugin hook."""
import json
import re
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import STRONG, make_user
from apps.content.models import Page, Post, PublishStatus
from apps.core import identity
from apps.core.forms import SiteSettingsForm
from apps.core.models import SiteSettings
from apps.plugins.hooks import sanitize_jsonld

SCRIPT = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
SITE = "https://ravixcms.com"


def graphs(response):
    """Every JSON-LD block of the page, parsed; the nodes of all @graph lists, by @type."""
    html = response.content.decode()
    blocks = [json.loads(raw) for raw in SCRIPT.findall(html)]
    nodes = [n for b in blocks for n in b.get("@graph", [b])]
    return html, blocks, {n["@type"]: n for n in nodes}


def mk_post(slug, **kw):
    kw.setdefault("status", PublishStatus.PUBLISHED)
    kw.setdefault("language", "en")
    return Post.objects.create(title=kw.pop("title", f"Post {slug}"), slug=slug, body="body text", **kw)


@override_settings(SITE_URL=SITE)
class JsonLdTests(TestCase):
    def setUp(self):
        cache.clear()
        cfg = SiteSettings.load()
        cfg.site_name = "Ravix"
        cfg.tagline = "A CMS"
        cfg.description = "The site description"
        cfg.save()

    def test_home_has_website_and_organization_with_stable_ids(self):
        html, blocks, nodes = graphs(self.client.get("/en/"))
        self.assertEqual(blocks[0]["@context"], "https://schema.org")
        self.assertEqual(nodes["Organization"]["@id"], f"{SITE}/#organization")
        self.assertEqual(nodes["WebSite"]["@id"], f"{SITE}/#website")
        self.assertEqual(nodes["WebSite"]["publisher"], {"@id": f"{SITE}/#organization"})
        self.assertEqual(nodes["WebSite"]["inLanguage"], "en")
        self.assertEqual(nodes["Organization"]["name"], "Ravix")
        action = nodes["WebSite"]["potentialAction"]
        self.assertEqual(action["@type"], "SearchAction")
        self.assertIn("{search_term_string}", action["target"]["urlTemplate"])
        # the same ids on every language's home page
        _html, _blocks, fa = graphs(self.client.get("/fa/"))
        self.assertEqual(fa["Organization"]["@id"], nodes["Organization"]["@id"])
        self.assertEqual(fa["WebSite"]["@id"], nodes["WebSite"]["@id"])
        self.assertEqual(fa["WebSite"]["inLanguage"], "fa")

    def test_persian_and_english_output_are_valid_json(self):
        cfg = SiteSettings.load()
        cfg.site_name = "راویکس"
        cfg.translations = {"en": {"site_name": "Ravix"}}
        cfg.save()
        _h, _b, fa = graphs(self.client.get("/fa/"))
        _h, _b, en = graphs(self.client.get("/en/"))
        self.assertEqual(fa["WebSite"]["name"], "راویکس")  # Persian text is written as is, not as \u escapes
        self.assertEqual(en["WebSite"]["name"], "Ravix")
        self.assertIn("راویکس", self.client.get("/fa/").content.decode())

    def test_identity_settings_flow_into_the_organization(self):
        cfg = SiteSettings.load()
        cfg.identity_name = "Ravix CMS"
        cfg.identity_alternate_names = ["راویکس", "Ravix"]
        cfg.identity_same_as = ["https://github.com/ravixcms", "https://x.com/ravixcms"]
        cfg.identity_description = "An open CMS."
        cfg.save()
        _h, _b, nodes = graphs(self.client.get("/en/"))
        org = nodes["Organization"]
        self.assertEqual(org["name"], "Ravix CMS")
        self.assertEqual(org["alternateName"], ["راویکس", "Ravix"])
        self.assertEqual(org["sameAs"], ["https://github.com/ravixcms", "https://x.com/ravixcms"])
        self.assertEqual(org["description"], "An open CMS.")
        self.assertEqual(org["url"], SITE + "/")

    def test_person_type_uses_a_person_node(self):
        cfg = SiteSettings.load()
        cfg.identity_type = "person"
        cfg.save()
        _h, _b, nodes = graphs(self.client.get("/en/"))
        self.assertEqual(nodes["Person"]["@id"], f"{SITE}/#person")
        self.assertNotIn("Organization", nodes)
        self.assertEqual(nodes["WebSite"]["publisher"], {"@id": f"{SITE}/#person"})

    def test_logo_from_the_media_library(self):
        from django.core.files.base import ContentFile

        from apps.media_library.models import Media

        media = Media(original_name="logo.png", kind=Media.Kind.IMAGE)
        media.file.save("logo.png", ContentFile(b"x"), save=True)
        cfg = SiteSettings.load()
        cfg.identity_logo = media
        cfg.save()
        _h, _b, nodes = graphs(self.client.get("/en/"))
        self.assertTrue(nodes["Organization"]["logo"].startswith(SITE + "/"))
        self.assertTrue(nodes["Organization"]["logo"].endswith(".png"))

    def test_post_has_blogposting_breadcrumbs_and_the_same_publisher(self):
        user = make_user("author", Role.ADMIN)
        p = mk_post("hello", author=user, excerpt="Short summary")
        _h, _b, nodes = graphs(self.client.get(f"/en/blog/{p.slug}/"))
        article = nodes["BlogPosting"]
        self.assertEqual(article["headline"], "Post hello")
        self.assertEqual(article["inLanguage"], "en")
        self.assertEqual(article["description"], "Short summary")
        self.assertEqual(article["publisher"]["@id"], f"{SITE}/#organization")
        self.assertEqual(article["isPartOf"], {"@id": f"{SITE}/#website"})
        self.assertEqual(article["mainEntityOfPage"]["@id"], article["url"])
        self.assertTrue(article["url"].startswith(SITE + "/en/"))
        for key in ("datePublished", "dateModified", "author"):
            self.assertIn(key, article)
        self.assertEqual(article["author"]["@type"], "Person")
        crumbs = nodes["BreadcrumbList"]["itemListElement"]
        self.assertEqual([c["position"] for c in crumbs], [1, 2, 3])
        self.assertEqual(crumbs[-1]["item"], article["url"])
        self.assertEqual(article["breadcrumb"], {"@id": nodes["BreadcrumbList"]["@id"]})

    def test_page_has_webpage_and_breadcrumbs(self):
        pg = Page.objects.create(title="About", slug="about", body="b", status=PublishStatus.PUBLISHED, language="en")
        _h, _b, nodes = graphs(self.client.get(f"/en/{pg.slug}/"))
        self.assertEqual(nodes["WebPage"]["name"], "About")
        self.assertEqual(nodes["WebPage"]["isPartOf"], {"@id": f"{SITE}/#website"})
        self.assertEqual(len(nodes["BreadcrumbList"]["itemListElement"]), 2)

    def test_a_title_with_a_closing_script_tag_cannot_break_out(self):
        p = mk_post("evil", title='</script><script>alert(1)</script> <!-- & "q"')
        html = self.client.get(f"/en/blog/{p.slug}/").content.decode()
        raw = SCRIPT.findall(html)
        self.assertTrue(raw)
        for chunk in raw:
            self.assertNotIn("</script", chunk.lower())
            self.assertNotIn("<!--", chunk)
            self.assertNotIn("<", chunk)
        data = json.loads(raw[0])
        node = next(n for n in data["@graph"] if n["@type"] == "BlogPosting")
        self.assertEqual(node["headline"], '</script><script>alert(1)</script> <!-- & "q"')  # the data itself is unchanged
        self.assertNotIn("<script>alert(1)", html)

    def test_identity_name_with_markup_is_escaped_too(self):
        cfg = SiteSettings.load()
        cfg.identity_name = "A</script><b>x"
        cfg.save()
        html = self.client.get("/en/").content.decode()
        for chunk in SCRIPT.findall(html):
            self.assertNotIn("<", chunk)

    def test_noindex_and_draft_posts_get_no_json_ld(self):
        hidden = mk_post("hidden", noindex=True)
        draft = mk_post("draft", status=PublishStatus.DRAFT)
        self.assertEqual(SCRIPT.findall(self.client.get(f"/en/blog/{hidden.slug}/").content.decode()), [])
        self.assertEqual(self.client.get(f"/en/blog/{draft.slug}/").status_code, 404)

    def test_translations_are_listed_as_work_translation(self):
        from apps.content import translations

        en, fa = mk_post("hello", language="en"), mk_post("salam", language="fa")
        translations.link([en, fa])
        _h, _b, nodes = graphs(self.client.get(f"/en/blog/{en.slug}/"))
        other = nodes["BlogPosting"]["workTranslation"]
        self.assertEqual([o["inLanguage"] for o in other], ["fa"])
        self.assertTrue(other[0]["url"].endswith(f"/fa/blog/{fa.slug}/"))
        _h, _b, alone = graphs(self.client.get(f"/en/blog/{mk_post('solo').slug}/"))
        self.assertNotIn("workTranslation", alone["BlogPosting"])

    def test_json_ld_works_with_the_shop_disabled(self):
        with override_settings(SHOP_ENABLED=False):
            _h, _b, nodes = graphs(self.client.get("/en/"))
        self.assertIn("WebSite", nodes)


class HookTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_plugin_can_add_a_node_and_cannot_break_the_page(self):
        def add(nodes, request=None, **ctx):
            return nodes + [{"@type": "SoftwareApplication", "name": "Ravix", "offers": {"@type": "Offer", "price": "0"}}]

        with mock.patch("apps.public.jsonld.apply_filter", side_effect=lambda name, value, **ctx: sanitize_jsonld(value, add(value))):
            _h, _b, nodes = graphs(self.client.get("/en/"))
        self.assertIn("SoftwareApplication", nodes)
        self.assertIn("WebSite", nodes)

    def test_hook_is_registered(self):
        from apps.plugins.hooks_registry import HOOKS

        self.assertEqual(HOOKS["public.jsonld"], "filter")

    def test_sanitizer_ignores_bad_answers(self):
        base = [{"@type": "WebSite"}]
        for bad in ("text", None, {"@type": "X"}, [1], [{"name": "no type"}], [{"@type": 5}], [{"@type": "X", "v": object()}],
                    [{"@type": "X"}] * 31, [{"@type": "X", "big": "a" * 70000}], [{"@type": "X", "n": float("nan")}]):
            self.assertEqual(sanitize_jsonld(base, bad), base, bad)

    def test_sanitizer_drops_context_and_keeps_good_nodes(self):
        out = sanitize_jsonld([], [{"@context": "https://evil", "@type": ["A", "B"], "x": 1}])
        self.assertEqual(out, [{"@type": ["A", "B"], "x": 1}])


class IdentityFormTests(TestCase):
    def test_same_as_accepts_only_full_https_addresses(self):
        self.assertEqual(identity.clean_profiles("https://a.example/x\n\nhttps://a.example/x\n https://b.example "), ["https://a.example/x", "https://b.example"])
        for bad in ("http://a.example", "javascript:alert(1)", "ravix", "//a.example", "https://", "https://user:pw@a.example", "https://a.example/<x>", "ftp://a.example", "https://localhost"):
            with self.assertRaises(Exception, msg=bad):
                identity.clean_profiles(bad)
        with self.assertRaises(Exception):
            identity.clean_profiles("\n".join(f"https://a{i}.example" for i in range(21)))

    def test_names_are_trimmed_and_limited(self):
        self.assertEqual(identity.clean_names(" A \n\nA\nB  c "), ["A", "B c"])
        with self.assertRaises(Exception):
            identity.clean_names("x" * 121)
        with self.assertRaises(Exception):
            identity.clean_names("\n".join(str(i) for i in range(11)))

    def test_settings_form_saves_and_rejects(self):
        cfg = SiteSettings.load()
        form = SiteSettingsForm(self._data(cfg, identity_same_as="http://bad.example"), instance=cfg)
        self.assertFalse(form.is_valid())
        self.assertIn("identity_same_as", form.errors)
        form = SiteSettingsForm(self._data(cfg, identity_same_as="https://good.example", identity_alternate_names="One\nTwo"), instance=cfg)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.identity_same_as, ["https://good.example"])
        self.assertEqual(saved.identity_alternate_names, ["One", "Two"])
        self.assertEqual(SiteSettingsForm(instance=saved).initial["identity_alternate_names"], "One\nTwo")

    def test_settings_page_shows_the_card(self):
        make_user("boss", Role.ADMIN)
        self.client.login(username="boss", password=STRONG)
        response = self.client.get(reverse("core:site_settings"))
        self.assertContains(response, "identity_same_as")

    @staticmethod
    def _data(cfg, **extra):
        form = SiteSettingsForm(instance=cfg)
        data = {}
        for name, field in form.fields.items():
            value = form.initial.get(name, field.initial)
            if value is None or value is False:
                continue
            data[name] = value
        data.update({"site_name": "Ravix", "language": "en", "identity_type": "organization"})
        data.update(extra)
        return data
