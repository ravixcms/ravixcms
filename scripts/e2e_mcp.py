"""End-to-end test of Ravix MCP with the OFFICIAL MCP Python SDK as the client (needs ``pip install "mcp>=2,<3" playwright``).

A real server is started (SQLite, no internet). The SDK discovers the server, registers itself, sends a real browser through sign-in and the
consent page, redeems the code with PKCE, lists the tools and calls one — over the 2026-07-28 protocol and again over the older handshake
protocol. Then a person ends the connection from "Connected apps" and the token stops working.

Run:  RAVIX_PYTHON=/path/to/python CHROMIUM_PATH=... python scripts/e2e_mcp.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.metadata
import http.server
import os
import shutil
import socket
import ssl
import subprocess
import sys
import secrets
import tempfile
import threading
import time
from functools import partial
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
from mcp import Client
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PY = os.environ.get("RAVIX_PYTHON") or sys.executable
PASSWORD = "Sup3r-Secret-Pass!"
results: list[tuple[str, bool]] = []

SEED = '''
from django.contrib.auth import get_user_model
from apps.accounts.permissions import Role
from apps.mcp.models import McpSettings
McpSettings.objects.update_or_create(pk=1, defaults={"enabled": True})
get_user_model().objects.create_user("boss", "boss@example.com", "Sup3r-Secret-Pass!", role=Role.SUPER_ADMIN)
print("seeded")
'''


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("  ✔ " if ok else "  ✘ ") + name + ("" if ok else f"   ← {detail}"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Memory:
    """In-memory token storage for the SDK."""

    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.info: OAuthClientInformationFull | None = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.info

    async def set_client_info(self, info):
        self.info = info


class CallbackServer:
    """A real little web server standing in for the app's redirect address (what a real client does), so the test never depends on how a
    browser-automation tool treats redirects. ``tls`` serves https with a throw-away certificate for the host name ``fake_host``."""

    def __init__(self, tls: bool = False, fake_host: str = "127.0.0.1", tmp: Path | None = None):
        self.hits: list[str] = []
        self.event = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if "code=" in self.path:  # a browser also asks for /favicon.ico; only the redirect carries the code
                    outer.hits.append(self.path)
                    outer.event.set()
                body = b"connected"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.tls = tls
        self.host = fake_host
        if tls:
            crt, key = tmp / "cb.crt", tmp / "cb.key"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={fake_host}",
                            "-addext", f"subjectAltName=DNS:{fake_host}"], check=True, capture_output=True)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(crt, key)
            self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def origin(self) -> str:
        return f"{'https' if self.tls else 'http'}://{self.host}:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def with_browser(fn, *args, resolver_rules: str = ""):
    """Playwright's sync API cannot share a thread with asyncio, so every browser step runs in its own worker thread with its own browser."""
    with sync_playwright() as pw:
        kw = {"headless": not os.environ.get("HEADED")}
        if os.environ.get("CHROMIUM_PATH"):
            kw["executable_path"] = os.environ["CHROMIUM_PATH"]
        kw["args"] = ["--proxy-server=direct://", "--proxy-bypass-list=*"]  # every address here is on this machine; a proxy in the environment must not get in the way
        if resolver_rules:
            kw["args"].append(f"--host-resolver-rules={resolver_rules}")
        browser = pw.chromium.launch(**kw)
        print(f"   (browser: Chromium {browser.version}, Playwright {importlib.metadata.version('playwright')})")
        try:
            return fn(*args, browser)
        finally:
            browser.close()


def browser_approve(auth_url: str, cb: CallbackServer, expect_host: str, browser) -> AuthorizationCodeResult:
    """What the person does: sign in, read the page, tick "I trust" (the callback is an address Ravix does not know), press Allow.
    The browser must then really arrive at the app's address: ``cb`` records the request it receives."""
    ctx = browser.new_context(ignore_https_errors=cb.tls)
    page = ctx.new_page()
    consent: dict = {}
    page.on("response", lambda r: consent.update(csp=r.headers.get("content-security-policy", "")) if r.request.method == "GET" and "/oauth/authorize" in r.url and r.status == 200 else None)
    page.goto(auth_url)
    if "/accounts/login" in page.url:
        page.fill("#id_username", "boss@example.com")
        page.fill("#id_password", PASSWORD)
        page.click("form:has(#id_password) button[type=submit]")
        page.wait_for_load_state()
    check("the consent page shows the address the person returns to", page.locator(f"text={expect_host}").count() >= 1)
    check("an address Ravix does not know is flagged in red", page.locator(".alert-danger").count() >= 1)
    form_action = next((d for d in consent.get("csp", "").split(";") if d.strip().startswith("form-action")), "")
    check("the page lets a form lead only to itself and the app's own address", form_action.split() == ["form-action", "'self'", cb.origin], form_action)
    page.check("input[name=trust_host]")
    page.click("button[name=decision][value=allow]")
    arrived = cb.event.wait(15)
    check("after Allow the browser really arrives at the app's address", arrived, page.url)
    ctx.close()
    if not arrived:
        raise SystemExit("the browser never reached the redirect address")
    q = parse_qs(urlsplit(cb.hits[0]).query)
    return AuthorizationCodeResult(code=q["code"][0], state=q.get("state", [None])[0], iss=q.get("iss", [None])[0])


def end_connection(base: str, browser) -> None:
    """The person opens "Connected apps" in their account and ends the connection."""
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(base + "/accounts/login/")
    page.fill("#id_username", "boss@example.com")
    page.fill("#id_password", PASSWORD)
    page.click("form:has(#id_password) button[type=submit]")
    page.wait_for_load_state()
    page.goto(base + "/accounts/connected-apps/")
    check("the connected app is listed with its name", page.locator("text=Ravix E2E Client").count() >= 1)
    page.click("button:has-text('End connection'), button.btn-danger")
    page.wait_for_load_state()
    ctx.close()


async def run(base: str, tmp: Path):
    mcp_url = base + "/mcp/"
    cb = CallbackServer()  # a real web server on a free port
    cb_origin = cb.origin
    storage = Memory()
    pending: dict = {}

    async def redirect_handler(url: str):
        pending["result"] = await asyncio.to_thread(with_browser, browser_approve, url, cb, "127.0.0.1")

    async def callback_handler():
        return pending["result"]

    meta = OAuthClientMetadata(client_name="Ravix E2E Client", redirect_uris=[f"{cb_origin}/callback"], grant_types=["authorization_code", "refresh_token"],
                               response_types=["code"], token_endpoint_auth_method="none", scope="site:read offline_access")
    provider = OAuthClientProvider(server_url=mcp_url, client_metadata=meta, storage=storage, redirect_handler=redirect_handler, callback_handler=callback_handler)

    print("\n[1] The official SDK client connects (modern protocol, discovered automatically)")
    async with Client(streamable_http_client(mcp_url, http_client=create_mcp_http_client(auth=provider))) as client:
        check("the SDK negotiated the 2026-07-28 protocol", getattr(client, "protocol_version", None) == "2026-07-28", getattr(client, "protocol_version", None))
        listing = await client.list_tools()
        names = [t.name for t in listing.tools]
        check("tools/list returns exactly get_site_status", names == ["get_site_status"], names)
        tool = listing.tools[0]
        check("the tool is annotated read-only", tool.annotations is not None and tool.annotations.read_only_hint is True)
        result = await client.call_tool("get_site_status", {})
        data = result.structured_content or {}
        check("tools/call returns the site status", not result.is_error and "verdict" in data and data.get("checks"), result)
    check("the SDK registered itself and got a token with a refresh token", storage.info is not None and storage.tokens is not None and bool(storage.tokens.refresh_token))
    check("the token is limited to what was granted", (storage.tokens.scope or "").split() == ["site:read", "offline_access"], storage.tokens.scope)

    print("\n[2] The same token over the older handshake protocol (initialize)")
    async with Client(streamable_http_client(mcp_url, http_client=create_mcp_http_client(auth=provider)), mode="legacy") as legacy:
        check("a handshake-era version was negotiated without a session", getattr(legacy, "protocol_version", "") in ("2025-11-25", "2025-06-18", "2025-03-26"), getattr(legacy, "protocol_version", None))
        check("tools/list works", [t.name for t in (await legacy.list_tools()).tools] == ["get_site_status"])
        res = await legacy.call_tool("get_site_status", {})
        check("tools/call works", not res.is_error)

    print("\n[3] Plain HTTP behaviour a client may rely on")
    with httpx.Client(base_url=base) as http:
        no_token = http.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        check("no token gives 401 with the metadata address", no_token.status_code == 401 and "resource_metadata=" in no_token.headers.get("www-authenticate", ""))
        check("GET on the endpoint is 405", http.get("/mcp/").status_code == 405)
        meta_doc = http.get("/.well-known/oauth-authorization-server").json()
        check("the authorization server metadata advertises S256 and CIMD", meta_doc["code_challenge_methods_supported"] == ["S256"] and meta_doc["client_id_metadata_document_supported"])

    print("\n[4] Ending the connection from the account page")
    await asyncio.to_thread(with_browser, end_connection, base)
    with httpx.Client(base_url=base) as http:
        resp = http.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Authorization": f"Bearer {storage.tokens.access_token}"})
        check("the access token is dead the moment the person ends the connection", resp.status_code == 401, resp.status_code)
        refreshed = http.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": storage.tokens.refresh_token, "client_id": storage.info.client_id})
        check("the refresh token is dead too", refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant")

    print("\n[5] An app on another website (https, its own origin) — the browser must really get back to it after Allow")
    ext = CallbackServer(tls=True, fake_host="claude-fake.test", tmp=tmp)
    try:
        redirect = ext.origin + "/api/mcp/auth_callback"
        with httpx.Client(base_url=base) as http:
            reg = http.post("/oauth/register", json={"client_name": "Fake claude.ai", "redirect_uris": [redirect], "token_endpoint_auth_method": "none",
                                                     "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
            cid = reg.json()["client_id"]
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            state = secrets.token_urlsafe(16)
            query = httpx.QueryParams({"response_type": "code", "client_id": cid, "redirect_uri": redirect, "code_challenge": challenge, "code_challenge_method": "S256",
                                       "state": state, "scope": "site:read", "resource": mcp_url})
            got = await asyncio.to_thread(partial(with_browser, browser_approve, f"{base}/oauth/authorize?{query}", ext, "claude-fake.test", resolver_rules="MAP claude-fake.test 127.0.0.1"))
            check("the redirect carries the state and the issuer", got.state == state and got.iss == base, (got.state, got.iss))
            tok = http.post("/oauth/token", data={"grant_type": "authorization_code", "code": got.code, "redirect_uri": redirect, "client_id": cid, "code_verifier": verifier, "resource": mcp_url})
            check("the code from the other website's redirect is redeemed", tok.status_code == 200 and tok.json().get("access_token"), tok.text[:200])
            init = http.post("/mcp/", headers={"Authorization": "Bearer " + tok.json()["access_token"]},
                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
            check("that token works on /mcp/", init.status_code == 200 and "result" in init.json(), init.text[:200])
    finally:
        ext.stop()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ravix-e2e-mcp-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    rel = os.path.relpath(tmp / "e2e.sqlite3", ROOT)
    env = {**os.environ, "DATABASE_URL": "sqlite:///" + rel, "DJANGO_SETTINGS_MODULE": "config.settings.development", "PYTHONUNBUFFERED": "1", "RAVIX_INSTALLER_ENABLED": "false"}
    server = None
    try:
        subprocess.run([PY, "manage.py", "migrate", "-v0"], cwd=ROOT, env=env, check=True)
        subprocess.run([PY, "manage.py", "shell", "-v0"], cwd=ROOT, env=env, input=SEED.encode(), check=True)
        with open(tmp / "server.log", "wb") as out:
            server = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env, stdout=out, stderr=subprocess.STDOUT)
            for _ in range(60):
                try:
                    socket.create_connection(("127.0.0.1", port), 0.5).close()
                    break
                except OSError:
                    time.sleep(0.5)
            asyncio.run(run(base, tmp))
    finally:
        if server:
            server.terminate()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + ("" if not failed else "; failed: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
