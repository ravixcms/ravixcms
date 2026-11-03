import json
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.audit.models import AuditLog
from apps.mcp import tokens
from apps.mcp.models import AuthorizationCode, McpConnection, McpSettings, McpToken, OAuthClient

from .helpers import REDIRECT, RESOURCE, McpTestCase, make_user, pkce


class OffByDefaultTests(McpTestCase):
    ADDRESSES = ("/mcp/", "/mcp", "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-authorization-server",
                 "/oauth/authorize", "/oauth/token", "/oauth/register", "/oauth/revoke", "/accounts/connected-apps/")

    def test_a_fresh_install_has_it_switched_off(self):
        McpSettings.objects.all().delete()
        self.assertFalse(McpSettings.load().enabled)

    def test_everything_answers_404_while_off_for_any_method(self):
        McpSettings.objects.update(enabled=False)
        self.http.force_login(self.boss)
        for path in self.ADDRESSES:
            for method in ("get", "post"):
                self.assertEqual(getattr(self.http, method)(path).status_code, 404, (method, path))

    @override_settings(RAVIX_MULTISITE_ENABLED=True)
    def test_it_stays_off_when_several_sites_share_the_install(self):
        self.assertEqual(self.http.get("/.well-known/oauth-authorization-server").status_code, 404)
        self.assertEqual(self.http.post("/mcp/").status_code, 404)


class DiscoveryTests(McpTestCase):
    def test_protected_resource_metadata(self):
        data = self.http.get("/.well-known/oauth-protected-resource").json()
        self.assertEqual(data["resource"], RESOURCE)
        self.assertEqual(data["authorization_servers"], ["http://testserver"])
        self.assertIn("site:read", data["scopes_supported"])
        self.assertEqual(self.http.get("/.well-known/oauth-protected-resource/mcp").status_code, 200)

    def test_authorization_server_metadata(self):
        data = self.http.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(data["issuer"], "http://testserver")
        self.assertEqual(data["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(data["token_endpoint_auth_methods_supported"], ["none"])
        self.assertTrue(data["client_id_metadata_document_supported"])
        self.assertTrue(data["authorization_response_iss_parameter_supported"])
        for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
            self.assertTrue(data[key].startswith("http://testserver/oauth/"), key)

    @override_settings(SITE_URL="https://site.example.com")
    def test_the_configured_site_address_is_the_issuer(self):
        data = self.http.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(data["issuer"], "https://site.example.com")
        self.assertEqual(data["token_endpoint"], "https://site.example.com/oauth/token")


class RegistrationTests(McpTestCase):
    def test_a_public_client_can_register(self):
        resp = self.register()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data["client_id"].startswith("rvx_c_"))
        self.assertEqual(data["token_endpoint_auth_method"], "none")
        self.assertNotIn("client_secret", data)

    def test_secrets_are_not_supported(self):
        resp = self.register(token_endpoint_auth_method="client_secret_basic")
        self.assertEqual((resp.status_code, resp.json()["error"]), (400, "invalid_client_metadata"))

    def test_bad_redirect_addresses_are_refused(self):
        for bad in ("http://evil.example.com/cb", "javascript:alert(1)", "https://claude.ai/cb#frag", "ftp://x.example/cb", "/relative", "https://user:pw@claude.ai/cb", "data:text/html,x"):
            self.assertEqual(self.register(bad).status_code, 400, bad)

    def test_loopback_and_private_use_schemes_are_accepted(self):
        for good in ("http://127.0.0.1:33418/callback", "http://localhost:8000/cb", "com.example.app:/oauth"):
            self.assertEqual(self.register(good).status_code, 201, good)

    def test_registration_is_rate_limited_per_address(self):
        codes = [self.register().status_code for _ in range(22)]
        self.assertEqual(codes[:20], [201] * 20)
        self.assertEqual(codes[20], 429)

    def test_a_bad_body_is_an_error_not_a_crash(self):
        resp = self.http.post("/oauth/register", data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_too_many_unused_registrations_are_refused(self):
        from apps.mcp import clients

        with mock.patch.object(clients, "MAX_UNUSED_CLIENTS", 1):
            self.assertEqual(self.register().status_code, 201)
            self.assertEqual(self.register().status_code, 429)


class AuthorizeTests(McpTestCase):
    def setUp(self):
        super().setUp()
        self.cid = self.client_id()

    def test_anonymous_people_are_sent_to_sign_in_and_back(self):
        params, _ = self.authorize_params(self.cid)
        resp = self.http.get("/oauth/authorize", params)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp["Location"])
        self.assertIn("next=", resp["Location"])

    def test_an_unknown_client_is_an_error_page_not_a_redirect(self):
        params, _ = self.authorize_params("rvx_c_nope")
        self.http.force_login(self.boss)
        resp = self.http.get("/oauth/authorize", params)
        self.assertEqual(resp.status_code, 400)

    def test_a_forged_redirect_address_is_never_followed(self):
        params, _ = self.authorize_params(self.cid, redirect="https://evil.example.com/cb")
        self.http.force_login(self.boss)
        resp = self.http.get("/oauth/authorize", params)
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("Location", resp)
        resp = self.http.post("/oauth/authorize", {**params, "decision": "allow"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(AuthorizationCode.objects.count(), 0)

    def test_the_redirect_address_must_match_exactly(self):
        params, _ = self.authorize_params(self.cid, redirect=REDIRECT + "/extra")
        self.http.force_login(self.boss)
        self.assertEqual(self.http.get("/oauth/authorize", params).status_code, 400)

    def test_pkce_s256_is_required(self):
        self.http.force_login(self.boss)
        params, _ = self.authorize_params(self.cid)
        for patch in ({"code_challenge_method": "plain"}, {"code_challenge_method": ""}, {"code_challenge": "short"}, {"code_challenge": ""}):
            resp = self.http.get("/oauth/authorize", {**params, **patch})
            self.assertEqual(resp.status_code, 302, patch)
            q = parse_qs(urlsplit(resp["Location"]).query)
            self.assertEqual(q["error"], ["invalid_request"])
            self.assertEqual(q["iss"], ["http://testserver"])
            self.assertTrue(resp["Location"].startswith(REDIRECT))

    def test_the_resource_must_be_this_sites_endpoint(self):
        self.http.force_login(self.boss)
        params, _ = self.authorize_params(self.cid, resource="https://other.example.com/mcp/")
        q = parse_qs(urlsplit(self.http.get("/oauth/authorize", params)["Location"]).query)
        self.assertEqual(q["error"], ["invalid_target"])

    def test_the_resource_without_the_slash_is_the_same_resource(self):
        self.http.force_login(self.boss)
        params, _ = self.authorize_params(self.cid, resource="http://testserver/mcp")
        self.assertEqual(self.http.get("/oauth/authorize", params).status_code, 200)

    def test_the_consent_page_shows_the_address_it_returns_to(self):
        self.http.force_login(self.boss)
        params, _ = self.authorize_params(self.cid)
        resp = self.http.get("/oauth/authorize", params)
        self.assertContains(resp, "claude.ai")
        self.assertEqual(resp["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", resp["Content-Security-Policy"])
        self.assertIn("form-action 'self' https://claude.ai", resp["Content-Security-Policy"])
        self.assertNotContains(resp, "trust_host")  # a known address needs no extra confirmation

    def test_an_unknown_address_gets_a_warning_and_needs_confirmation(self):
        cid = self.client_id("https://apps.example.org/cb", "Claude")
        params, _ = self.authorize_params(cid, redirect="https://apps.example.org/cb")
        self.http.force_login(self.boss)
        resp = self.http.get("/oauth/authorize", params)
        self.assertContains(resp, "apps.example.org")
        self.assertContains(resp, "alert-danger")
        self.assertContains(resp, 'name="trust_host"')
        resp = self.http.post("/oauth/authorize", {**params, "decision": "allow"})
        self.assertEqual(resp.status_code, 200)  # shown again, no code issued
        self.assertEqual(AuthorizationCode.objects.count(), 0)
        resp = self.http.post("/oauth/authorize", {**params, "decision": "allow", "trust_host": "apps.example.org"})
        self.assertEqual(resp.status_code, 302)

    def test_trusted_only_mode_blocks_unknown_addresses(self):
        McpSettings.objects.update(unknown_apps="trusted_only")
        cid = self.client_id("https://apps.example.org/cb")
        params, _ = self.authorize_params(cid, redirect="https://apps.example.org/cb")
        self.http.force_login(self.boss)
        self.assertContains(self.http.get("/oauth/authorize", params), "only allows apps from trusted addresses")
        resp = self.http.post("/oauth/authorize", {**params, "decision": "allow", "trust_host": "apps.example.org"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AuthorizationCode.objects.count(), 0)
        McpSettings.objects.update(trusted_hosts="apps.example.org")
        self.assertEqual(self.http.post("/oauth/authorize", {**params, "decision": "allow"}).status_code, 302)

    def test_deny_returns_access_denied(self):
        params, _ = self.authorize_params(self.cid)
        resp = self.approve(self.boss, params, decision="deny")
        q = parse_qs(urlsplit(resp["Location"]).query)
        self.assertEqual((q["error"], q["state"], q["iss"]), (["access_denied"], ["st4te"], ["http://testserver"]))

    def test_allow_returns_a_code_state_and_issuer(self):
        params, _ = self.authorize_params(self.cid)
        resp = self.approve(self.boss, params)
        q = parse_qs(urlsplit(resp["Location"]).query)
        self.assertTrue(resp["Location"].startswith(REDIRECT + "?"))
        self.assertEqual((q["state"], q["iss"]), (["st4te"], ["http://testserver"]))
        self.assertTrue(q["code"][0].startswith("rvx_ac_"))
        row = AuthorizationCode.objects.get()
        self.assertNotEqual(row.code_hash, q["code"][0])  # only a hash is stored
        self.assertLessEqual((row.expires_at - timezone.now()).total_seconds(), 60)

    def test_the_consent_form_needs_the_csrf_token(self):
        params, _ = self.authorize_params(self.cid)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.boss)
        self.assertEqual(strict.post("/oauth/authorize", {**params, "decision": "allow"}).status_code, 403)

    def test_an_author_cannot_connect_until_the_superadmin_allows_it(self):
        author = make_user(Role.AUTHOR)
        params, _ = self.authorize_params(self.cid, scope="content:read")
        c = Client()
        c.force_login(author)
        page = c.get("/oauth/authorize", params)
        self.assertContains(page, "not allowed to give an app any of the access")
        self.assertNotContains(page, 'value="allow"')
        resp = c.post("/oauth/authorize", {**params, "decision": "allow"})
        self.assertEqual(AuthorizationCode.objects.count(), 0)
        McpSettings.objects.update(author_scopes=["content:read"])
        self.assertEqual(c.post("/oauth/authorize", {**params, "decision": "allow"}).status_code, 302)

    def test_a_manager_cannot_publish_unless_allowed_but_a_superadmin_can(self):
        manager = make_user(Role.ADMIN)
        params, _ = self.authorize_params(self.cid, scope="site:read content:publish")
        q = self.code_from(self.approve(manager, params))
        self.assertEqual(AuthorizationCode.objects.get().scope, "site:read")
        AuthorizationCode.objects.all().delete()
        q = self.code_from(self.approve(self.boss, params))
        self.assertEqual(AuthorizationCode.objects.get().scope, "site:read content:publish")


class TokenTests(McpTestCase):
    def exchange(self, cid, code, verifier, **over):
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": cid, "code_verifier": verifier, **over}
        return self.http.post("/oauth/token", data)

    def fresh_code(self, scope="site:read offline_access"):
        cid = self.client_id()
        params, verifier = self.authorize_params(cid, scope=scope)
        code = self.code_from(self.approve(self.boss, params))["code"][0]
        return cid, code, verifier

    def test_a_full_exchange(self):
        cid, code, verifier = self.fresh_code()
        resp = self.exchange(cid, code, verifier)
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((data["token_type"], data["expires_in"], data["scope"]), ("Bearer", 1800, "site:read offline_access"))
        self.assertTrue(data["access_token"].startswith("rvx_at_") and data["refresh_token"].startswith("rvx_rt_"))
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_tokens_are_stored_only_as_hashes(self):
        data = self.connect()
        stored = set(McpToken.objects.values_list("token_hash", flat=True))
        self.assertNotIn(data["access_token"], stored)
        self.assertIn(tokens.digest(data["access_token"]), stored)

    def test_no_refresh_token_without_offline_access(self):
        cid, code, verifier = self.fresh_code("site:read")
        self.assertNotIn("refresh_token", self.exchange(cid, code, verifier).json())

    def test_the_wrong_verifier_fails(self):
        cid, code, _ = self.fresh_code()
        resp = self.exchange(cid, code, pkce()[0])
        self.assertEqual((resp.status_code, resp.json()["error"]), (400, "invalid_grant"))

    def test_a_missing_or_malformed_verifier_fails(self):
        cid, code, _ = self.fresh_code()
        for bad in ("", "short", "x" * 200):
            self.assertEqual(self.exchange(cid, code, bad).json()["error"], "invalid_grant")

    def test_a_code_cannot_be_used_twice_and_the_second_use_kills_the_first_tokens(self):
        cid, code, verifier = self.fresh_code()
        first = self.exchange(cid, code, verifier).json()
        second = self.exchange(cid, code, verifier)
        self.assertEqual(second.json()["error"], "invalid_grant")
        rpc = self.rpc(first["access_token"], "tools/list")
        self.assertEqual(rpc.status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action="mcp", message__icontains="twice").exists())

    def test_an_expired_code_fails(self):
        cid, code, verifier = self.fresh_code()
        AuthorizationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(cid, code, verifier).json()["error"], "invalid_grant")

    def test_the_code_is_bound_to_the_client_and_the_redirect_address(self):
        cid, code, verifier = self.fresh_code()
        other = self.client_id()
        self.assertEqual(self.exchange(other, code, verifier).json()["error"], "invalid_grant")
        self.assertEqual(self.exchange(cid, code, verifier, redirect_uri=REDIRECT + "/x").json()["error"], "invalid_grant")
        self.assertEqual(self.exchange(cid, code, verifier, resource="https://other.example.com/mcp/").json()["error"], "invalid_target")
        self.assertEqual(self.exchange(cid, code, verifier).status_code, 200)

    def test_unknown_grant_types_are_refused(self):
        self.assertEqual(self.http.post("/oauth/token", {"grant_type": "password"}).json()["error"], "unsupported_grant_type")
        self.assertEqual(self.http.post("/oauth/token", {"grant_type": "client_credentials"}).json()["error"], "unsupported_grant_type")

    def test_refresh_rotates_and_reuse_ends_the_connection(self):
        data = self.connect()
        cid = data["client_id"]
        r1 = self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": cid}).json()
        self.assertNotEqual(r1["refresh_token"], data["refresh_token"])
        self.assertEqual(self.rpc(r1["access_token"], "tools/list").status_code, 200)
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)  # the old access token went with the rotation
        again = self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": cid})
        self.assertEqual(again.json()["error"], "invalid_grant")
        self.assertEqual(self.rpc(r1["access_token"], "tools/list").status_code, 401)  # reuse ended the whole connection
        self.assertEqual(self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": r1["refresh_token"], "client_id": cid}).json()["error"], "invalid_grant")

    def test_a_refresh_token_belongs_to_one_client(self):
        data = self.connect()
        other = self.client_id()
        resp = self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": other})
        self.assertEqual(resp.json()["error"], "invalid_grant")

    def test_a_refresh_cannot_widen_the_scopes(self):
        data = self.connect()
        resp = self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": data["client_id"], "scope": "site:read content:publish"})
        self.assertEqual(resp.json()["error"], "invalid_scope")

    def test_an_idle_connection_expires(self):
        data = self.connect()
        McpToken.objects.filter(kind="refresh").update(expires_at=timezone.now() - timedelta(seconds=1))
        resp = self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": data["client_id"]})
        self.assertEqual(resp.json()["error"], "invalid_grant")

    def test_a_new_approval_replaces_the_old_connection(self):
        first = self.connect()
        second = self.connect()
        self.assertEqual(self.rpc(second["access_token"], "tools/list").status_code, 200)
        self.assertEqual(McpConnection.objects.filter(revoked_at__isnull=True).count(), 2)  # two different clients registered, so both live
        cid = first["client_id"]
        params, verifier = self.authorize_params(cid)
        code = self.code_from(self.approve(self.boss, params))["code"][0]
        self.http.post("/oauth/token", {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": cid, "code_verifier": verifier})
        self.assertEqual(self.rpc(first["access_token"], "tools/list").status_code, 401)

    def test_revoking_a_refresh_token_ends_the_connection(self):
        data = self.connect()
        resp = self.http.post("/oauth/revoke", {"token": data["refresh_token"], "client_id": data["client_id"]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)

    def test_revoking_with_the_wrong_client_or_garbage_changes_nothing(self):
        data = self.connect()
        self.assertEqual(self.http.post("/oauth/revoke", {"token": data["refresh_token"], "client_id": "rvx_c_other"}).status_code, 200)
        self.assertEqual(self.http.post("/oauth/revoke", {"token": "garbage", "client_id": data["client_id"]}).status_code, 200)
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 200)

    def test_revoking_an_access_token_only_kills_that_token(self):
        data = self.connect()
        self.http.post("/oauth/revoke", {"token": data["access_token"], "client_id": data["client_id"]})
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)
        refreshed = self.http.post("/oauth/token", {"grant_type": "refresh_token", "refresh_token": data["refresh_token"], "client_id": data["client_id"]})
        self.assertEqual(refreshed.status_code, 200)

    def test_token_requests_are_rate_limited_per_address(self):
        codes = [self.http.post("/oauth/token", {"grant_type": "authorization_code"}).status_code for _ in range(243)]
        self.assertEqual(codes[-1], 429)
