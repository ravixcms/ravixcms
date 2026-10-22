import io
import json
import shutil
import tempfile
import zipfile

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from apps.accounts.permissions import Role
from apps.accounts.tests.test_auth import make_user
from apps.pluginhub.models import Category, Publisher, PublisherToken


def manifest(slug="demo_plugin", version="1.0.0", **over):
    m = {"slug": slug, "name": "افزونهٔ نمونه", "version": version, "description": "توضیح", "author": "ناشر", "api_version": 1,
         "ravix": ">=0.5.0,<1.0.0", "category": "tools", "license": "MIT"}
    m.update(over)
    return m


def make_zip(slug="demo_plugin", version="1.0.0", *, extra=None, man=None, root=None, skip_manifest=False, raw_entries=None):
    buf = io.BytesIO()
    root = root or slug
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if not skip_manifest:
            zf.writestr(f"{root}/plugin.json", json.dumps(man or manifest(slug, version)))
        zf.writestr(f"{root}/__init__.py", "")
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
        for info, content in (raw_entries or []):
            zf.writestr(info, content)
    return buf.getvalue()


class HubTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self._tmp = tempfile.mkdtemp(prefix="hub-test-")
        self._ov = override_settings(RAVIX_REGISTRY_STORAGE=self._tmp)
        self._ov.enable()
        self.super = make_user("root", Role.SUPER_ADMIN)
        self.admin = make_user("boss", Role.ADMIN)
        self.author = make_user("writer", Role.AUTHOR)
        self.pubuser = make_user("pub1", Role.AUTHOR)
        self.pubuser2 = make_user("pub2", Role.USER)
        self.pub = Publisher.objects.create(user=self.pubuser, display_name="ناشر یک")
        self.pub2 = Publisher.objects.create(user=self.pubuser2, display_name="ناشر دو")
        self.tool_cat = Category.objects.get(slug="tools")

    def tearDown(self):
        self._ov.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def login(self, user):
        c = Client()
        c.force_login(user)
        return c

    def token(self, publisher=None):
        return PublisherToken.issue(publisher or self.pub, "ci")[1]

    def api_publish(self, data, token=None, secure=True, **post):
        f = io.BytesIO(data)
        f.name = "pkg.zip"
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return Client().post("/registry/v1/publish/", {"package": f, **post}, secure=secure, **headers)

    def publish_version(self, slug="demo_plugin", version="1.0.0", publisher=None, **kw):
        from apps.pluginhub import services
        pub = publisher or self.pub
        return services.upload_version(pub, pub.user, make_zip(slug, version, **kw), publish=True)
