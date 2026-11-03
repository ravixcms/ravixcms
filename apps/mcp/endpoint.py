"""POST /mcp/ — the one address an AI assistant talks to."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt

from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.core.clientip import client_ip

from . import addresses, protocol, ratelimit, scopes as sc, tokens, tools
from .models import McpConnection, McpSettings, McpToken

logger = logging.getLogger(__name__)
MAX_BODY = 4 * 1024 * 1024
TOUCH_EVERY = timedelta(seconds=60)


def _challenge(request, *, error: str = "", description: str = "", scope: str = "") -> str:
    parts = [f'Bearer resource_metadata="{addresses.absolute(request, "mcp:resource_metadata")}"']
    if error:
        parts.append(f'error="{error}"')
    if description:
        parts.append(f'error_description="{description}"')
    parts.append(f'scope="{scope or sc.join(s for s in sc.SCOPES if s != sc.OFFLINE_ACCESS)}"')
    return ", ".join(parts)


def _json(payload, status=200, **headers):
    resp = JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False, "separators": (",", ":")})
    resp["Cache-Control"] = "no-store"
    for key, value in headers.items():
        resp[key.replace("_", "-")] = value
    return resp


def _unauthorized(request, error="", description=""):
    resp = _json({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": description or "Authentication required"}}, 401)
    resp["WWW-Authenticate"] = _challenge(request, error=error, description=description)
    return resp


def origin_allowed(request, cfg) -> bool:
    """DNS-rebinding guard: a request from a web page must come from this site or from an origin the administrator listed. No Origin = not a browser."""
    origin = request.META.get("HTTP_ORIGIN")
    if origin is None:
        return True
    origin = origin.rstrip("/").lower()
    parts = urlsplit(addresses.issuer(request))
    own = f"{parts.scheme}://{parts.netloc}".lower()
    return origin == own or origin in cfg.origin_list()


class Authenticated:
    def __init__(self, connection: McpConnection, token: McpToken, cfg: McpSettings):
        self.connection, self.token, self.cfg = connection, token, cfg
        self.user = connection.user
        self.scopes = sc.effective(self.user, connection.scope_list, cfg)


def authenticate(request, cfg: McpSettings):
    """Returns an Authenticated or a response to send back."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        return _unauthorized(request)
    row = McpToken.objects.select_related("connection__user", "connection__client").filter(token_hash=tokens.digest(value), kind=McpToken.ACCESS).first()
    if row is None:
        return _unauthorized(request, "invalid_token", "The access token is not valid")
    if row.expires_at < timezone.now():
        return _unauthorized(request, "invalid_token", "The access token expired")
    conn = row.connection
    user = conn.user
    if not conn.active:
        return _unauthorized(request, "invalid_token", "The connection was revoked")
    if not addresses.same_resource(conn.resource, addresses.resource(request)):
        return _unauthorized(request, "invalid_token", "The token was not issued for this resource")
    if not user.is_active or getattr(user, "must_change_password", False):
        return _unauthorized(request, "invalid_token", "The account cannot use this connection")
    if user.password_changed_at and user.password_changed_at > conn.created_at:
        conn.revoke("password_changed")
        return _unauthorized(request, "invalid_token", "The password changed; connect again")
    return Authenticated(conn, row, cfg)


def _touch(conn: McpConnection, request) -> None:
    now = timezone.now()
    if conn.last_used_at is None or now - conn.last_used_at > TOUCH_EVERY:
        McpConnection.objects.filter(pk=conn.pk).update(last_used_at=now, last_ip=client_ip(request))


@csrf_exempt
def endpoint(request):
    cfg = McpSettings.load()
    if not cfg.is_active:
        raise Http404()
    if request.method != "POST":
        resp = HttpResponse(status=405)
        resp["Allow"] = "POST"
        return resp
    if not origin_allowed(request, cfg):
        return _json({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Origin not allowed"}}, 403)

    auth = authenticate(request, cfg)
    if not isinstance(auth, Authenticated):
        return auth
    conn = auth.connection

    ok, retry = ratelimit.allow_connection(conn.pk, "calls", cfg.rate_per_minute)
    if not ok:
        return _json({"jsonrpc": "2.0", "id": None, "error": {"code": -32029, "message": "Too many requests"}}, 429, Retry_After=str(retry))

    declared = request.META.get("CONTENT_LENGTH") or ""
    if declared.isdigit() and int(declared) > MAX_BODY:
        return _json({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request body too large"}}, 413)
    raw = request.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        return _json({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request body too large"}}, 413)
    try:
        message = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _json({"jsonrpc": "2.0", "id": None, "error": {"code": protocol.E_PARSE, "message": "Parse error"}}, 400)

    _touch(conn, request)
    ctx = tools.Context(request=request, user=auth.user, connection=conn, scopes=auth.scopes)
    tool_name = None
    try:
        status, body, tool_name = protocol.dispatch(message, request.headers, lambda: ctx)
    except protocol.ScopeMissing as miss:
        _audit(request, auth, message, "denied")
        resp = _json({"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                      "error": {"code": -32003, "message": "Insufficient scope", "data": {"required": miss.needed}}}, 403)
        resp["WWW-Authenticate"] = _challenge(request, error="insufficient_scope", description="More access is needed for this tool", scope=sc.join(miss.needed + list(conn.scope_list)))
        return resp
    except protocol.Failure as exc:
        return _json(exc.body(), exc.status)
    except Exception:  # noqa: BLE001 — a bug must not leak details to the caller
        logger.exception("MCP request failed")
        return _json({"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None, "error": {"code": protocol.E_INTERNAL, "message": "Internal error"}}, 500)

    if tool_name:
        failed = bool(body and body.get("result", {}).get("isError"))
        _audit(request, auth, message, "error" if failed else "ok")
    if body is None:
        return HttpResponse(status=status)
    return _json(body, status)


def _audit(request, auth: Authenticated, message, outcome: str) -> None:
    params = message.get("params") if isinstance(message, dict) and isinstance(message.get("params"), dict) else {}
    name = str(params.get("name", ""))[:80]
    # The tool, the app and the outcome are recorded. The arguments are not: they can be long and can hold private text.
    words = {"ok": _("succeeded"), "error": _("failed"), "denied": _("refused: the access it needs was not granted")}
    log_event(request, AuditLog.Action.MCP, None, (_("Tool %(tool)s called by “%(app)s”: %(outcome)s") % {"tool": name, "app": auth.connection.client.name, "outcome": words[outcome]})[:400],
              object_type=_("AI assistant"), actor=auth.user)
