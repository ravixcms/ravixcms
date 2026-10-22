import os

from django.test import Client, override_settings

from apps.pluginhub.models import HubPlugin, HubVersion

from .helpers import HubTestCase, make_zip, manifest


class PublicApiTests(HubTestCase):
    def setUp(self):
        super().setUp()
        self.v1 = self.publish_version("alpha_plugin", "1.0.0")
        self.v2 = self.publish_version("alpha_plugin", "1.2.0")
        self.publish_version("beta_plugin", "2.0.0", man=manifest("beta_plugin", "2.0.0", ravin=">=3.0.0,<4.0.0", category="seo", description="جستجوی ویژه"))
        self.c = Client()

    def get(self, url, **kw):
        return self.c.get(url, secure=True, **kw)

    def test_meta(self):
        d = self.get("/registry/v1/").json()
        self.assertEqual(d["api_version"], 1)
        self.assertEqual(d["integrity"]["algorithm"], "sha256")

    def test_list_returns_required_fields(self):
        d = self.get("/registry/v1/plugins/").json()
        self.assertEqual(d["count"], 2)
        a = next(r for r in d["results"] if r["id"] == "alpha_plugin")
        for k in ("name", "summary", "latest_version", "author", "compatible", "category", "latest"):
            self.assertIn(k, a)
        self.assertEqual(a["latest_version"], "1.2.0")
        self.assertEqual(a["latest"]["sha256"], self.v2.sha256)

    def test_search_and_category(self):
        self.assertEqual(self.get("/registry/v1/plugins/?q=ویژه").json()["count"], 1)
        self.assertEqual(self.get("/registry/v1/plugins/?category=seo").json()["count"], 1)
        self.assertEqual(self.get("/registry/v1/plugins/?category=nope").json()["count"], 0)

    def test_compatible_filter(self):
        d = self.get("/registry/v1/plugins/?compatible=1").json()
        self.assertEqual([r["id"] for r in d["results"]], ["alpha_plugin"])
        all_ = self.get("/registry/v1/plugins/").json()["results"]
        beta = next(r for r in all_ if r["id"] == "beta_plugin")
        self.assertFalse(beta["compatible"])

    def test_pagination_and_bad_params(self):
        d = self.get("/registry/v1/plugins/?page_size=1&page=2").json()
        self.assertEqual(len(d["results"]), 1)
        self.assertEqual(self.get("/registry/v1/plugins/?page=x").status_code, 400)
        self.assertEqual(self.get("/registry/v1/plugins/?page_size=999").json()["page_size"], 50)

    def test_detail_has_versions_with_compat(self):
        d = self.get("/registry/v1/plugins/beta_plugin/").json()
        v = d["versions"][0]
        self.assertFalse(v["compatibility"]["compatible"])
        self.assertTrue(v["compatibility"]["problems"])
        self.assertEqual(v["manifest"]["slug"], "beta_plugin")
        self.assertEqual(self.get("/registry/v1/plugins/nope/").status_code, 404)

    def test_drafts_are_hidden(self):
        from apps.pluginhub import services
        services.upload_version(self.pub, self.pubuser, make_zip("draft_plugin", "1.0.0"))
        self.assertEqual(self.get("/registry/v1/plugins/draft_plugin/").status_code, 404)
        self.assertEqual(self.get("/registry/v1/plugins/").json()["count"], 2)
        services.upload_version(self.pub, self.pubuser, make_zip("alpha_plugin", "1.3.0"))
        d = self.get("/registry/v1/plugins/alpha_plugin/").json()
        self.assertNotIn("1.3.0", [v["version"] for v in d["versions"]])
        self.assertEqual(self.get("/registry/v1/plugins/alpha_plugin/versions/1.3.0/").status_code, 404)

    def test_download_integrity_headers(self):
        r = self.get("/registry/v1/plugins/alpha_plugin/versions/1.2.0/download/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["X-Checksum-SHA256"], self.v2.sha256)
        self.assertEqual(r["Content-Type"], "application/zip")
        body = b"".join(r.streaming_content)
        import hashlib
        self.assertEqual(hashlib.sha256(body).hexdigest(), self.v2.sha256)
        self.v2.refresh_from_db()
        self.assertEqual(self.v2.downloads, 1)

    def test_download_requires_https(self):
        r = self.c.get("/registry/v1/plugins/alpha_plugin/versions/1.2.0/download/")  # بدون secure
        self.assertEqual(r.status_code, 403)
        with override_settings(RAVIN_REGISTRY_ALLOW_INSECURE_DOWNLOAD=True):
            self.assertEqual(self.c.get("/registry/v1/plugins/alpha_plugin/versions/1.2.0/download/").status_code, 200)

    def test_download_urls_are_https(self):
        d = self.get("/registry/v1/plugins/alpha_plugin/").json()
        self.assertTrue(all(v["download_url"].startswith("https://") for v in d["versions"]))
        r = self.c.get("/registry/v1/plugins/alpha_plugin/")  # درخواست http
        self.assertTrue(r.json()["versions"][0]["download_url"].startswith("https://"))

    def test_tampered_file_is_never_served(self):
        path = self.v2.package.path
        with open(path, "ab") as fh:
            fh.write(b"evil")
        r = self.get("/registry/v1/plugins/alpha_plugin/versions/1.2.0/download/")
        self.assertEqual(r.status_code, 500)

    def test_yanked_not_downloadable(self):
        from apps.pluginhub import services
        services.yank_version(self.v1, self.pub, "باگ امنیتی")
        self.assertEqual(self.get("/registry/v1/plugins/alpha_plugin/versions/1.0.0/download/").status_code, 404)
        d = self.get("/registry/v1/plugins/alpha_plugin/versions/1.0.0/").json()
        self.assertIsNone(d["download_url"])
        self.assertEqual(d["yank_reason"], "باگ امنیتی")
        self.assertEqual(self.get("/registry/v1/plugins/alpha_plugin/").json()["latest_version"], "1.2.0")

    def test_unlisted_and_inactive_publisher_hidden(self):
        HubPlugin.objects.filter(slug="alpha_plugin").update(is_listed=False)
        self.assertEqual(self.get("/registry/v1/plugins/alpha_plugin/").status_code, 404)
        self.assertEqual(self.get("/registry/v1/plugins/alpha_plugin/versions/1.2.0/download/").status_code, 404)
        self.pub.is_active = False
        self.pub.save()
        self.assertEqual(self.get("/registry/v1/plugins/").json()["count"], 0)

    def test_registry_can_be_disabled(self):
        with override_settings(RAVIN_REGISTRY_ENABLED=False):
            self.assertEqual(self.get("/registry/v1/plugins/").status_code, 404)

    def test_write_methods_not_allowed_on_read_endpoints(self):
        self.assertEqual(self.c.post("/registry/v1/plugins/", secure=True).status_code, 405)

    def test_package_not_in_media_or_static(self):
        from django.conf import settings
        self.assertFalse(str(self.v2.package.path).startswith(str(settings.MEDIA_ROOT)))
        with self.assertRaises(ValueError):
            self.v2.package.url


class PublishApiTests(HubTestCase):
    def test_requires_token(self):
        self.assertEqual(self.api_publish(make_zip()).status_code, 401)
        self.assertEqual(self.api_publish(make_zip(), token="rvn_wrong").status_code, 401)

    def test_publish_requires_https(self):
        self.assertEqual(self.api_publish(make_zip(), token=self.token(), secure=False).status_code, 403)

    def test_publish_ok_and_draft_by_default(self):
        r = self.api_publish(make_zip(), token=self.token())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "draft")
        r = self.api_publish(make_zip(version="1.1.0"), token=self.token(), publish="1")
        self.assertEqual(r.json()["status"], "published")
        v = HubVersion.objects.get(version="1.1.0")
        self.assertEqual(len(v.sha256), 64)
        self.assertEqual(v.plugin.publisher, self.pub)

    def test_non_publisher_token_impossible_and_revoked_or_inactive_rejected(self):
        tok = self.token()
        self.pub.is_active = False
        self.pub.save()
        self.assertEqual(self.api_publish(make_zip(), token=tok).status_code, 401)
        self.pub.is_active = True
        self.pub.save()
        from django.utils import timezone
        self.pub.tokens.update(revoked_at=timezone.now())
        self.assertEqual(self.api_publish(make_zip(), token=tok).status_code, 401)

    def test_token_stored_hashed_only(self):
        from apps.pluginhub.models import PublisherToken
        tok = self.token()
        row = PublisherToken.objects.get()
        self.assertNotIn(tok, (row.token_hash, row.prefix, row.name))
        self.assertEqual(row.token_hash, PublisherToken.hash(tok))

    def test_versions_are_immutable(self):
        tok = self.token()
        self.assertEqual(self.api_publish(make_zip(), token=tok).status_code, 201)
        r = self.api_publish(make_zip(extra={"demo_plugin/x.py": "print(1)"}), token=tok)
        self.assertEqual(r.status_code, 400)
        self.assertIn("تغییرناپذیر", r.json()["error"])

    def test_cannot_hijack_other_publishers_slug(self):
        self.assertEqual(self.api_publish(make_zip(), token=self.token()).status_code, 201)
        r = self.api_publish(make_zip(version="9.9.9"), token=self.token(self.pub2))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(HubVersion.objects.count(), 1)

    def test_missing_file_and_oversize(self):
        tok = self.token()
        r = Client().post("/registry/v1/publish/", {}, secure=True, HTTP_AUTHORIZATION=f"Bearer {tok}")
        self.assertEqual(r.status_code, 400)
        with override_settings(RAVIN_REGISTRY_MAX_PACKAGE_BYTES=100):
            self.assertEqual(self.api_publish(make_zip(), token=tok).status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(Client().get("/registry/v1/publish/", secure=True).status_code, 405)

    def test_throttle(self):
        for _ in range(30):
            self.api_publish(b"x", token="rvn_x")
        self.assertEqual(self.api_publish(b"x", token="rvn_x").status_code, 429)
