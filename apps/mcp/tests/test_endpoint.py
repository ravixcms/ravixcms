import base64
import json
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.accounts.permissions import Role
from apps.audit.models import AuditLog
from apps.mcp import protocol, tools
from apps.mcp.models import McpConnection, McpSettings, McpToken

from .helpers import META, MODERN, McpTestCase, make_user


class AccessTests(McpTestCase):
    def test_no_token_gets_a_401_that_points_to_the_metadata(self):
        resp = self.http.post("/mcp/", data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 401)
        challenge = resp["WWW-Authenticate"]
        self.assertTrue(challenge.startswith("Bearer "))
        self.assertIn('resource_metadata="http://testserver/.well-known/oauth-protected-resource"', challenge)
        self.assertIn('scope="', challenge)

    def test_garbage_and_wrong_scheme_tokens_are_refused(self):
        for header in ("Bearer nope", "Basic abc", "Bearer ", "rvx_at_x"):
            resp = self.http.post("/mcp/", data="{}", content_type="application/json", HTTP_AUTHORIZATION=header)
            self.assertEqual(resp.status_code, 401, header)

    def test_an_expired_token_is_refused(self):
        data = self.connect()
        McpToken.objects.filter(kind="access").update(expires_at=timezone.now() - timedelta(seconds=1))
        resp = self.rpc(data["access_token"], "tools/list")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("invalid_token", resp["WWW-Authenticate"])

    def test_a_revoked_connection_is_refused_at_once(self):
        data = self.connect()
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 200)
        McpConnection.objects.get().revoke("test")
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)

    def test_a_token_for_another_resource_is_refused(self):
        data = self.connect()
        McpConnection.objects.update(resource="https://other.example.com/mcp/")
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)

    def test_a_refresh_token_is_not_an_access_token(self):
        data = self.connect()
        self.assertEqual(self.rpc(data["refresh_token"], "tools/list").status_code, 401)

    def test_a_deactivated_person_loses_access_at_once(self):
        data = self.connect()
        self.boss.is_active = False
        self.boss.save()
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)

    def test_a_person_who_must_change_their_password_loses_access(self):
        data = self.connect()
        self.boss.must_change_password = True
        self.boss.save()
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)

    def test_changing_the_password_ends_the_connection(self):
        data = self.connect()
        self.boss.mark_password_changed()
        self.boss.save()
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 401)
        self.assertEqual(McpConnection.objects.get().revoked_reason, "password_changed")

    def test_a_demotion_removes_power_on_the_next_call(self):
        manager = make_user(Role.ADMIN)
        data = self.connect(user=manager)
        names = [t["name"] for t in self.rpc(data["access_token"], "tools/list").json()["result"]["tools"]]
        self.assertEqual(names, ["get_site_status"])
        manager.role = Role.USER
        manager.save()
        resp = self.rpc(data["access_token"], "tools/list")
        self.assertEqual(resp.json()["result"]["tools"], [])
        call = self.rpc(data["access_token"], "tools/call", {"name": "get_site_status"})
        self.assertEqual(call.status_code, 403)

    def test_the_administrator_can_take_a_scope_back_from_a_role(self):
        manager = make_user(Role.ADMIN)
        data = self.connect(user=manager)
        McpSettings.objects.update(admin_scopes=["offline_access"])
        self.assertEqual(self.rpc(data["access_token"], "tools/list").json()["result"]["tools"], [])

    def test_switching_mcp_off_stops_everything_at_once(self):
        data = self.connect()
        McpSettings.objects.update(enabled=False)
        self.assertEqual(self.rpc(data["access_token"], "tools/list").status_code, 404)

    def test_only_post_is_accepted(self):
        for method in ("get", "delete", "put"):
            resp = getattr(self.http, method)("/mcp/")
            self.assertEqual(resp.status_code, 405, method)
            self.assertEqual(resp["Allow"], "POST")

    def test_a_foreign_browser_origin_is_refused(self):
        data = self.connect()
        resp = self.rpc(data["access_token"], "tools/list", extra={"HTTP_ORIGIN": "https://evil.example.com"})
        self.assertEqual(resp.status_code, 403)
        ok = self.rpc(data["access_token"], "tools/list", extra={"HTTP_ORIGIN": "http://testserver"})
        self.assertEqual(ok.status_code, 200)
        McpSettings.objects.update(allowed_origins="https://app.example.com")
        self.assertEqual(self.rpc(data["access_token"], "tools/list", extra={"HTTP_ORIGIN": "https://app.example.com"}).status_code, 200)

    def test_a_big_body_is_refused(self):
        data = self.connect()
        with mock.patch("apps.mcp.endpoint.MAX_BODY", 100):
            resp = self.http.post("/mcp/", data="x" * 500, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {data['access_token']}")
        self.assertEqual(resp.status_code, 413)

    def test_broken_json_and_batches_are_protocol_errors(self):
        data = self.connect()
        auth = {"HTTP_AUTHORIZATION": f"Bearer {data['access_token']}"}
        bad = self.http.post("/mcp/", data="{nope", content_type="application/json", **auth)
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, -32700))
        batch = self.http.post("/mcp/", data="[]", content_type="application/json", **auth)
        self.assertEqual((batch.status_code, batch.json()["error"]["code"]), (400, -32600))

    def test_calls_are_rate_limited_per_connection(self):
        data = self.connect()
        McpSettings.objects.update(rate_per_minute=10)
        codes = [self.rpc(data["access_token"], "tools/list").status_code for _ in range(12)]
        self.assertEqual(codes[:10], [200] * 10)
        self.assertEqual(codes[10], 429)
        resp = self.rpc(data["access_token"], "tools/list")
        self.assertIn("Retry-After", resp)
        other = self.connect()
        self.assertEqual(self.rpc(other["access_token"], "tools/list").status_code, 200)  # another connection has its own allowance

    def test_a_person_cannot_use_somebody_elses_connection(self):
        a, b = make_user(Role.ADMIN), make_user(Role.ADMIN)
        da = self.connect(user=a)
        self.assertEqual(McpConnection.objects.get(user=a).user_id, a.pk)
        self.assertEqual(self.rpc(da["access_token"], "tools/call", {"name": "get_site_status"}).status_code, 200)


class ModernProtocolTests(McpTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.connect()["access_token"]

    def test_discover(self):
        r = self.rpc(self.token, "server/discover").json()["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(r["supportedVersions"][0], MODERN)
        self.assertIn("2025-11-25", r["supportedVersions"])
        self.assertIn("tools", r["capabilities"])
        self.assertEqual(r["serverInfo"]["name"], "ravix")
        self.assertIn("ttlMs", r)
        self.assertIn("cacheScope", r)

    def test_tools_list_is_cacheable_in_a_private_way_and_stable(self):
        a = self.rpc(self.token, "tools/list").json()["result"]
        b = self.rpc(self.token, "tools/list").json()["result"]
        self.assertEqual(a["tools"], b["tools"])
        self.assertEqual((a["resultType"], a["cacheScope"]), ("complete", "private"))
        self.assertGreater(a["ttlMs"], 0)
        tool = a["tools"][0]
        self.assertEqual(tool["annotations"], {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
        self.assertEqual(tool["inputSchema"], {"type": "object", "properties": {}, "additionalProperties": False})

    def test_a_tool_call_returns_structured_content_and_the_same_text(self):
        r = self.rpc(self.token, "tools/call", {"name": "get_site_status"}).json()["result"]
        self.assertEqual((r["resultType"], r["isError"]), ("complete", False))
        self.assertEqual(json.loads(r["content"][0]["text"]), r["structuredContent"])
        sc = r["structuredContent"]
        self.assertIn("verdict", sc)
        self.assertTrue(sc["checks"])
        self.assertEqual(set(sc["checks"][0]), {"group", "title", "status"})
        self.assertEqual(r["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "ravix")

    def test_the_status_never_contains_server_paths(self):
        text = self.rpc(self.token, "tools/call", {"name": "get_site_status"}).json()["result"]["content"][0]["text"]
        from django.conf import settings

        self.assertNotIn(str(settings.BASE_DIR), text)
        for needle in ("/home/", "SECRET", "password"):
            self.assertNotIn(needle, text)

    def test_header_mismatches_are_rejected(self):
        for extra in ({"HTTP_MCP_PROTOCOL_VERSION": "2025-11-25"}, {"HTTP_MCP_METHOD": "tools/list"}, {"HTTP_MCP_NAME": "other"}):
            resp = self.rpc(self.token, "tools/call", {"name": "get_site_status"}, extra=extra)
            self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (400, -32020), extra)

    def test_missing_headers_are_rejected(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": META}}
        auth = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        resp = self.http.post("/mcp/", data=json.dumps(body), content_type="application/json", **auth)
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (400, -32020))

    def test_a_base64_encoded_name_header_is_decoded(self):
        encoded = "=?base64?" + base64.b64encode(b"get_site_status").decode() + "?="
        resp = self.rpc(self.token, "tools/call", {"name": "get_site_status"}, extra={"HTTP_MCP_NAME": encoded})
        self.assertEqual(resp.status_code, 200)

    def test_an_unsupported_version_lists_the_supported_ones(self):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "1900-01-01"}}}
        resp = self.http.post("/mcp/", data=json.dumps(body), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}",
                              HTTP_MCP_PROTOCOL_VERSION="1900-01-01", HTTP_MCP_METHOD="tools/list")
        err = resp.json()["error"]
        self.assertEqual((resp.status_code, err["code"]), (400, -32022))
        self.assertEqual(err["data"]["requested"], "1900-01-01")
        self.assertIn(MODERN, err["data"]["supported"])

    def test_an_unknown_header_version_is_rejected_for_old_style_requests(self):
        resp = self.rpc(self.token, "tools/list", modern=False, extra={"HTTP_MCP_PROTOCOL_VERSION": "1900-01-01"})
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (400, -32022))

    def test_the_modern_header_without_metadata_is_a_mismatch(self):
        resp = self.rpc(self.token, "tools/list", modern=False, extra={"HTTP_MCP_PROTOCOL_VERSION": MODERN})
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (400, -32020))

    def test_unknown_methods_are_404_with_the_protocol_error(self):
        for method in ("resources/list", "prompts/list", "ping", "initialize", "logging/setLevel"):
            resp = self.rpc(self.token, method)
            self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (404, -32601), method)

    def test_a_session_header_is_ignored_and_never_minted(self):
        resp = self.rpc(self.token, "tools/list", extra={"HTTP_MCP_SESSION_ID": "abc"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Mcp-Session-Id", resp)

    def test_notifications_are_accepted_with_202(self):
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        resp = self.http.post("/mcp/", data=json.dumps(body), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual((resp.status_code, resp.content), (202, b""))

    def test_unknown_tools_and_bad_arguments(self):
        resp = self.rpc(self.token, "tools/call", {"name": "install_plugin"})
        self.assertEqual(resp.json()["error"]["code"], -32602)
        bad = self.rpc(self.token, "tools/call", {"name": "get_site_status", "arguments": {"x": 1}}).json()["result"]
        self.assertTrue(bad["isError"])
        self.assertIn("not a known field", bad["content"][0]["text"])

    def test_the_tool_needs_its_scope(self):
        narrow = self.connect(scope="content:read offline_access")  # a scope the manager has but none that opens get_site_status
        self.assertEqual(self.rpc(narrow["access_token"], "tools/list").json()["result"]["tools"], [])
        resp = self.rpc(narrow["access_token"], "tools/call", {"name": "get_site_status"})
        self.assertEqual(resp.status_code, 403)
        self.assertIn('error="insufficient_scope"', resp["WWW-Authenticate"])
        self.assertEqual(resp.json()["error"]["data"]["required"], ["site:read"])

    def test_every_call_is_audited_without_its_arguments(self):
        self.rpc(self.token, "tools/call", {"name": "get_site_status"})
        row = AuditLog.objects.filter(action="mcp").latest("pk")
        self.assertIn("get_site_status", row.message)
        self.assertIn("Claude", row.message)
        self.assertEqual(row.actor_id, self.boss.pk)

    def test_a_broken_tool_is_an_internal_error_without_details(self):
        boom = tools.Tool(name="boom", title="b", description="b", scopes=("site:read",), handler=lambda ctx: 1 / 0)
        tools._REGISTRY.append(boom)
        try:
            resp = self.rpc(self.token, "tools/call", {"name": "boom"})
        finally:
            tools._REGISTRY.remove(boom)
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn("division", resp.content.decode())


class LegacyProtocolTests(McpTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.connect()["access_token"]

    def test_initialize_negotiates_without_a_session(self):
        for version in ("2025-11-25", "2025-06-18", "2025-03-26"):
            resp = self.rpc(self.token, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}}, modern=False)
            r = resp.json()["result"]
            self.assertEqual(r["protocolVersion"], version)
            self.assertNotIn("resultType", r)
            self.assertNotIn("Mcp-Session-Id", resp)
        r = self.rpc(self.token, "initialize", {"protocolVersion": "1999-01-01"}, modern=False).json()["result"]
        self.assertEqual(r["protocolVersion"], "2025-11-25")

    def test_ping_and_tools_list_and_call(self):
        hdr = {"HTTP_MCP_PROTOCOL_VERSION": "2025-11-25"}
        self.assertEqual(self.rpc(self.token, "ping", modern=False, extra=hdr).json()["result"], {})
        listing = self.rpc(self.token, "tools/list", modern=False, extra=hdr).json()["result"]
        self.assertEqual([t["name"] for t in listing["tools"]], ["get_site_status"])
        self.assertNotIn("ttlMs", listing)
        call = self.rpc(self.token, "tools/call", {"name": "get_site_status", "arguments": {}}, modern=False, extra=hdr).json()["result"]
        self.assertFalse(call["isError"])
        self.assertNotIn("resultType", call)

    def test_a_request_without_a_version_header_is_treated_as_the_oldest_supported(self):
        self.assertEqual(self.rpc(self.token, "tools/list", modern=False).status_code, 200)

    def test_unknown_method_is_a_json_rpc_error_with_200(self):
        resp = self.rpc(self.token, "resources/list", modern=False)
        self.assertEqual((resp.status_code, resp.json()["error"]["code"]), (200, -32601))


class ToolListIsLockedTests(McpTestCase):
    EXPECTED_IN_M1 = ["get_site_status"]
    FORBIDDEN = ("plugin", "theme", "user", "role", "security", "key", "env", "email", "mail", "command", "exec", "shell", "order", "customer", "shop", "payment", "password", "token", "setting")

    def test_the_tool_list_is_exactly_what_this_release_ships(self):
        self.assertEqual([t.name for t in tools.all_tools()], self.EXPECTED_IN_M1)

    def test_no_tool_touches_what_must_never_be_exposed(self):
        for tool in tools.all_tools():
            low = tool.name.lower()
            for word in self.FORBIDDEN:
                self.assertNotIn(word, low, tool.name)

    def test_names_are_valid_and_every_tool_has_complete_annotations(self):
        import re

        for tool in tools.all_tools():
            self.assertRegex(tool.name, r"^[a-z][a-z0-9_]{0,63}$")
            self.assertEqual(set(tool.definition()["annotations"]), {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"})
            self.assertFalse(tool.input_schema.get("additionalProperties", True))
            self.assertTrue(tool.scopes)
            self.assertTrue(set(tool.scopes) <= set(__import__("apps.mcp.scopes", fromlist=["SCOPES"]).SCOPES))

    def test_the_read_only_tool_changes_nothing(self):
        from django.contrib.auth import get_user_model
        from apps.content.models import Page, Post

        token = self.connect()["access_token"]
        before = (get_user_model().objects.count(), Post.objects.count(), Page.objects.count())
        self.rpc(token, "tools/call", {"name": "get_site_status"})
        self.assertEqual(before, (get_user_model().objects.count(), Post.objects.count(), Page.objects.count()))
