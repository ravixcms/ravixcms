import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from apps.accounts.permissions import Role
from apps.core.models import SiteSettings
from apps.mcp import scopes as sc
from apps.mcp.models import McpSettings

User = get_user_model()
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://testserver/mcp/"
MODERN = "2026-07-28"
META = {"io.modelcontextprotocol/protocolVersion": MODERN, "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"}, "io.modelcontextprotocol/clientCapabilities": {}}
PASSWORD = "Xk9-vq7-Lm2-wR4"


def make_user(role=Role.SUPER_ADMIN, username=None, **extra):
    username = username or f"u{secrets.token_hex(3)}"
    return User.objects.create_user(username=username, email=f"{username}@example.com", password=PASSWORD, role=role, **extra)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class McpTestCase(TestCase):
    def setUp(self):
        cache.clear()
        site = SiteSettings.load()  # the messages these tests look for are English; pin the site language so the order of other tests cannot change it
        site.language = "en"
        site.save()
        cfg = McpSettings.load()
        cfg.enabled = True
        cfg.save()
        self.http = Client()
        self.boss = make_user(Role.SUPER_ADMIN, "boss")

    # ---- OAuth helpers
    def register(self, redirect=REDIRECT, name="Claude", **extra):
        resp = self.http.post("/oauth/register", data=json.dumps({"client_name": name, "redirect_uris": [redirect], **extra}), content_type="application/json")
        return resp

    def client_id(self, redirect=REDIRECT, name="Claude"):
        return self.register(redirect, name).json()["client_id"]

    def authorize_params(self, client_id, verifier_challenge=None, scope="site:read offline_access", redirect=REDIRECT, **extra):
        verifier, challenge = verifier_challenge or pkce()
        params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256",
                  "scope": scope, "state": "st4te", "resource": RESOURCE, **extra}
        return params, verifier

    def approve(self, user, params, **post):
        c = Client()
        c.force_login(user)
        resp = c.post("/oauth/authorize", {**params, "decision": "allow", **post})
        return resp

    def code_from(self, resp):
        assert resp.status_code == 302, resp.content[:300]
        return parse_qs(urlsplit(resp["Location"]).query)

    def connect(self, user=None, scope="site:read offline_access", redirect=REDIRECT):
        user = user or self.boss
        cid = self.client_id(redirect)
        params, verifier = self.authorize_params(cid, scope=scope, redirect=redirect)
        q = self.code_from(self.approve(user, params, trust_host=urlsplit(redirect).hostname))
        resp = self.http.post("/oauth/token", {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier})
        assert resp.status_code == 200, resp.content
        data = resp.json()
        data["client_id"] = cid
        return data

    # ---- MCP helpers
    def rpc(self, token, method, params=None, modern=True, rid=1, extra=None):
        params = dict(params or {})
        headers = dict(extra or {})
        if modern:
            params["_meta"] = META
            headers.setdefault("HTTP_MCP_PROTOCOL_VERSION", MODERN)
            headers.setdefault("HTTP_MCP_METHOD", method)
            if method == "tools/call":
                headers.setdefault("HTTP_MCP_NAME", params.get("name", ""))
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.http.post("/mcp/", data=json.dumps(body), content_type="application/json", **headers)
