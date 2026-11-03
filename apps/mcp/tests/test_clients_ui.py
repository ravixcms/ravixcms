import json
from unittest import mock

from datetime import timedelta

from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from apps.accounts.permissions import Role
from apps.mcp import clients, safefetch
from apps.mcp.models import McpConnection, McpSettings, OAuthClient

from .helpers import REDIRECT, McpTestCase, make_user

DOC_URL = "https://app.example.org/.well-known/client.json"


class AddressGuardTests(SimpleTestCase):
    def test_only_public_addresses_pass(self):
        for ip in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(safefetch.public_address(ip), ip)
        for ip in ("127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1", "::1", "fe80::1", "fc00::1",
                   "::ffff:127.0.0.1", "::ffff:10.0.0.1", "not-an-ip", ""):
            self.assertFalse(safefetch.public_address(ip), ip)

    def test_urls_are_checked_before_anything_is_resolved(self):
        for bad in ("http://example.com/x", "ftp://example.com/x", "https://user:pw@example.com/x", "https://example.com:8443/x", "https://127.0.0.1/x", "https://[::1]/x", "https:///x", ""):
            with self.assertRaises(safefetch.FetchError, msg=bad):
                safefetch.validate_url(bad)
        self.assertEqual(safefetch.validate_url("https://Example.com./a/b?c=1"), ("example.com", 443, "/a/b?c=1"))

    def test_a_name_that_resolves_to_a_private_address_is_refused(self):
        for answers in (["10.0.0.1"], ["127.0.0.1"], ["169.254.169.254"], ["8.8.8.8", "10.0.0.1"]):  # one private answer among public ones is enough to refuse
            with mock.patch.object(safefetch, "resolve", return_value=answers), mock.patch.object(safefetch, "_open") as opened:
                with self.assertRaises(safefetch.FetchError):
                    safefetch.fetch("https://evil.example.com/x")
                opened.assert_not_called()

    def test_the_connection_goes_to_the_address_that_was_checked(self):
        conn = mock.Mock()
        resp = mock.Mock(status=200)
        resp.getheader.side_effect = lambda name: {"Content-Length": "2", "Content-Type": "application/json; charset=utf-8"}.get(name)
        resp.read.return_value = b"{}"
        conn.getresponse.return_value = resp
        with mock.patch.object(safefetch, "resolve", return_value=["93.184.216.34"]), mock.patch.object(safefetch, "_open", return_value=conn) as opened:
            body, ctype = safefetch.fetch("https://example.com/doc")
        opened.assert_called_once_with("example.com", "93.184.216.34", 443, safefetch.DEFAULT_TIMEOUT)
        self.assertEqual((body, ctype), (b"{}", "application/json"))

    def test_redirects_big_bodies_and_errors_are_refused(self):
        def run(status=200, length=None, body=b"x"):
            conn = mock.Mock()
            resp = mock.Mock(status=status)
            resp.getheader.side_effect = lambda name: {"Content-Length": length}.get(name)
            resp.read.return_value = body
            conn.getresponse.return_value = resp
            with mock.patch.object(safefetch, "resolve", return_value=["93.184.216.34"]), mock.patch.object(safefetch, "_open", return_value=conn):
                return safefetch.fetch("https://example.com/doc", max_bytes=10)

        for kwargs in ({"status": 301}, {"status": 302}, {"status": 404}, {"length": "999"}, {"body": b"x" * 11}):
            with self.assertRaises(safefetch.FetchError, msg=kwargs):
                run(**kwargs)

    def test_network_failures_become_fetch_errors(self):
        conn = mock.Mock()
        conn.request.side_effect = OSError("boom")
        with mock.patch.object(safefetch, "resolve", return_value=["93.184.216.34"]), mock.patch.object(safefetch, "_open", return_value=conn):
            with self.assertRaises(safefetch.FetchError):
                safefetch.fetch("https://example.com/doc")


class ClientMetadataDocumentTests(TestCase):
    def doc(self, **over):
        return {"client_id": DOC_URL, "client_name": "Example App", "redirect_uris": ["https://app.example.org/cb"], "token_endpoint_auth_method": "none", **over}

    def fetch_returning(self, doc):
        return mock.patch.object(safefetch, "fetch", return_value=(json.dumps(doc).encode(), "application/json"))

    def test_a_valid_document_registers_the_client(self):
        with self.fetch_returning(self.doc()) as f:
            client = clients.resolve_client(DOC_URL)
        f.assert_called_once()
        self.assertEqual((client.kind, client.name, client.redirect_uris), ("cimd", "Example App", ["https://app.example.org/cb"]))

    def test_a_document_that_names_another_client_id_is_refused(self):
        with self.fetch_returning(self.doc(client_id="https://other.example.org/x")):
            with self.assertRaises(clients.ClientError):
                clients.resolve_client(DOC_URL)

    def test_secrets_bad_redirects_and_bad_json_are_refused(self):
        for doc in (self.doc(token_endpoint_auth_method="client_secret_post"), self.doc(redirect_uris=["http://evil.example/cb"]), self.doc(redirect_uris=[]), self.doc(redirect_uris="x")):
            with self.fetch_returning(doc):
                with self.assertRaises(clients.ClientError):
                    clients.resolve_client(DOC_URL)
        with mock.patch.object(safefetch, "fetch", return_value=(b"not json", "text/plain")):
            with self.assertRaises(clients.ClientError):
                clients.resolve_client(DOC_URL)

    def test_a_blocked_address_is_an_error_not_a_crash(self):
        with mock.patch.object(safefetch, "fetch", side_effect=safefetch.FetchError("private")):
            with self.assertRaises(clients.ClientError):
                clients.resolve_client("https://internal.example.org/client.json")

    def test_only_https_urls_with_a_path_can_be_client_ids(self):
        for bad in ("http://app.example.org/c.json", "https://app.example.org", "https://app.example.org/", "https://app.example.org/c?x=1", "relative", ""):
            with self.assertRaises(clients.ClientError, msg=bad):
                clients.resolve_client(bad)

    def test_a_fresh_copy_is_not_fetched_again_and_a_stale_one_survives_a_failure(self):
        with self.fetch_returning(self.doc()):
            clients.resolve_client(DOC_URL)
        with mock.patch.object(safefetch, "fetch") as f:
            clients.resolve_client(DOC_URL)
        f.assert_not_called()
        OAuthClient.objects.update(fetched_at=None)
        with mock.patch.object(safefetch, "fetch", side_effect=safefetch.FetchError("down")):
            self.assertEqual(clients.resolve_client(DOC_URL).name, "Example App")

    def test_names_are_cleaned_of_markup(self):
        with self.fetch_returning(self.doc(client_name="<script>alert(1)</script>Evil\n")):
            self.assertNotIn("<", clients.resolve_client(DOC_URL).name)


class ClientMetadataFlowTests(McpTestCase):
    def test_a_cimd_client_can_run_the_whole_flow(self):
        doc = {"client_id": DOC_URL, "client_name": "Example App", "redirect_uris": ["https://app.example.org/cb"], "token_endpoint_auth_method": "none"}
        with mock.patch.object(safefetch, "fetch", return_value=(json.dumps(doc).encode(), "application/json")):
            params, verifier = self.authorize_params(DOC_URL, redirect="https://app.example.org/cb")
            self.http.force_login(self.boss)
            page = self.http.get("/oauth/authorize", params)
            self.assertContains(page, "Example App")
            self.assertContains(page, "app.example.org")
        q = self.code_from(self.approve(self.boss, params, trust_host="app.example.org"))
        resp = self.http.post("/oauth/token", {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": "https://app.example.org/cb", "client_id": DOC_URL, "code_verifier": verifier})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.rpc(resp.json()["access_token"], "tools/list").status_code, 200)


class SettingsAndPagesTests(McpTestCase):
    def test_only_a_superadmin_reaches_the_settings(self):
        for role in (Role.ADMIN, Role.AUTHOR, Role.USER):
            c = Client()
            c.force_login(make_user(role))
            self.assertEqual(c.get("/panel/mcp/").status_code, 403, role)
            self.assertEqual(c.get("/panel/mcp/connections/").status_code, 403, role)
        self.http.force_login(self.boss)
        self.assertEqual(self.http.get("/panel/mcp/").status_code, 200)

    def test_the_settings_page_shows_the_address_to_give_the_assistant(self):
        self.http.force_login(self.boss)
        self.assertContains(self.http.get("/panel/mcp/"), "http://testserver/mcp/")

    def post_data(self, **over):
        data = {"enabled": "on", "unknown_apps": "warn", "trusted_hosts": "", "access_token_minutes": 30, "rate_per_minute": 120, "write_per_minute": 30,
                "allowed_origins": "", "admin_scopes": ["site:read", "offline_access"], "author_scopes": []}
        data.update(over)
        return data

    def test_saving_the_settings(self):
        self.http.force_login(self.boss)
        resp = self.http.post("/panel/mcp/", self.post_data(trusted_hosts="Apps.Example.org\napps.example.org", admin_scopes=["site:read"], author_scopes=["content:read"]))
        self.assertEqual(resp.status_code, 302)
        cfg = McpSettings.load()
        self.assertEqual((cfg.trusted_hosts, cfg.admin_scopes, cfg.author_scopes), ("apps.example.org", ["site:read"], ["content:read"]))

    def test_bad_hosts_and_origins_are_rejected(self):
        self.http.force_login(self.boss)
        self.assertEqual(self.http.post("/panel/mcp/", self.post_data(trusted_hosts="not a host")).status_code, 200)
        self.assertEqual(self.http.post("/panel/mcp/", self.post_data(allowed_origins="javascript:alert(1)")).status_code, 200)
        self.assertEqual(self.http.post("/panel/mcp/", self.post_data(access_token_minutes=1)).status_code, 200)

    def test_switching_it_on_and_off_is_audited(self):
        McpSettings.objects.update(enabled=False)
        self.http.force_login(self.boss)
        self.http.post("/panel/mcp/", self.post_data())
        self.assertTrue(McpSettings.load().enabled)
        self.assertTrue(self.http.get("/.well-known/oauth-authorization-server").status_code == 200)
        from apps.audit.models import AuditLog

        self.assertTrue(AuditLog.objects.filter(message__icontains="Ravix MCP settings").exists())

    def test_it_cannot_be_switched_on_with_several_sites(self):
        from django.test import override_settings

        McpSettings.objects.update(enabled=False)
        self.http.force_login(self.boss)
        with override_settings(RAVIX_MULTISITE_ENABLED=True):
            page = self.http.get("/panel/mcp/")
            self.assertContains(page, "Several sites share this installation")
            self.http.post("/panel/mcp/", self.post_data())
        self.assertFalse(McpSettings.load().enabled)

    def test_the_menu_item_is_for_superadmins_only(self):
        self.http.force_login(self.boss)
        self.assertContains(self.http.get("/panel/"), "/panel/mcp/")
        manager = Client()
        manager.force_login(make_user(Role.ADMIN))
        self.assertNotContains(manager.get("/panel/"), "/panel/mcp/")

    def test_a_person_sees_and_ends_only_their_own_connections(self):
        manager = make_user(Role.ADMIN)
        mine = self.connect(user=manager)
        theirs = self.connect(user=self.boss)
        c = Client()
        c.force_login(manager)
        page = c.get("/accounts/connected-apps/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Claude")
        mine_conn = McpConnection.objects.get(user=manager)
        boss_conn = McpConnection.objects.get(user=self.boss)
        self.assertEqual(c.post(f"/accounts/connected-apps/{boss_conn.pk}/revoke/").status_code, 404)
        self.assertEqual(self.rpc(theirs["access_token"], "tools/list").status_code, 200)
        self.assertEqual(c.post(f"/accounts/connected-apps/{mine_conn.pk}/revoke/").status_code, 302)
        self.assertEqual(self.rpc(mine["access_token"], "tools/list").status_code, 401)

    def test_the_account_tabs_show_the_apps_link_only_while_it_is_on(self):
        c = Client()
        c.force_login(self.boss)
        self.assertContains(c.get("/accounts/profile/"), "/accounts/connected-apps/")
        McpSettings.objects.update(enabled=False)
        self.assertNotContains(c.get("/accounts/profile/"), "/accounts/connected-apps/")
        self.assertEqual(c.get("/accounts/connected-apps/").status_code, 404)

    def test_the_administrator_sees_everyones_connections_and_can_end_one(self):
        manager = make_user(Role.ADMIN)
        data = self.connect(user=manager)
        self.http.force_login(self.boss)
        page = self.http.get("/panel/mcp/connections/")
        self.assertContains(page, manager.display_name)
        conn = McpConnection.objects.get(user=manager)
        self.assertEqual(self.http.post(f"/panel/mcp/connections/{conn.pk}/revoke/").status_code, 302)
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)
        self.assertEqual(McpConnection.objects.get(pk=conn.pk).revoked_reason, "revoked_by_admin")

    def test_ending_a_connection_needs_post(self):
        self.http.force_login(self.boss)
        data = self.connect()
        conn = McpConnection.objects.get()
        self.assertEqual(self.http.get(f"/panel/mcp/connections/{conn.pk}/revoke/").status_code, 405)


class HousekeepingTests(McpTestCase):
    def test_old_rows_are_removed_and_live_ones_stay(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.mcp.housekeeping import cleanup
        from apps.mcp.models import AuthorizationCode, McpToken

        live = self.connect()
        stale = self.connect()
        conn = McpConnection.objects.get(client__client_id=stale["client_id"])
        McpConnection.objects.filter(pk=conn.pk).update(last_used_at=timezone.now() - timedelta(days=91))
        unused = OAuthClient.objects.create(client_id="rvx_c_old", kind="dcr", name="Old", redirect_uris=[REDIRECT])
        OAuthClient.objects.filter(pk=unused.pk).update(created_at=timezone.now() - timedelta(days=8))
        cleanup()
        self.assertEqual(self.rpc(live["access_token"], "tools/list").status_code, 200)
        self.assertEqual(self.rpc(stale["access_token"], "tools/list").status_code, 401)
        self.assertFalse(OAuthClient.objects.filter(client_id="rvx_c_old").exists())
        McpConnection.objects.filter(pk=conn.pk).update(revoked_at=timezone.now() - timedelta(days=31))
        cleanup()
        self.assertFalse(McpConnection.objects.filter(pk=conn.pk).exists())

    def test_the_cron_job_runs_it(self):
        from django.core.management import call_command

        data = self.connect()
        from apps.mcp.models import McpToken

        McpToken.objects.filter(kind="access").update(expires_at=timezone.now() - timedelta(days=2))
        call_command("ravix_cron", "--no-backup", "--no-keep-alive", verbosity=0)
        self.assertFalse(McpToken.objects.filter(kind="access").exists())
        self.assertTrue(data["access_token"])
