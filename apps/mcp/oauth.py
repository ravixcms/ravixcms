"""The authorisation server: discovery documents, client registration, authorize + consent, token, revoke (OAuth 2.1, PKCE S256 only)."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.audit.models import AuditLog
from apps.audit.services import log_event

from . import addresses, clients, ratelimit, scopes as sc, tokens
from .models import AuthorizationCode, McpConnection, McpSettings, McpToken, OAuthClient

logger = logging.getLogger(__name__)
CODE_SECONDS = 60
REFRESH_DAYS = 90
SUPPORTED_SCOPES = sc.SCOPES


def _gate() -> McpSettings:
    cfg = McpSettings.load()
    if not cfg.is_active:
        raise Http404()
    return cfg


def gated(view):
    """Outermost: while Ravix MCP is off (or blocked by multisite) every address answers 404, whatever the method, so nothing is advertised."""
    import functools

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        _gate()
        return view(request, *args, **kwargs)

    return wrapper


def _no_store(resp):
    resp["Cache-Control"] = "no-store"
    resp["Pragma"] = "no-cache"
    return resp


def _json(payload: dict, status: int = 200, cors: bool = False) -> JsonResponse:
    resp = _no_store(JsonResponse(payload, status=status))
    if cors:
        resp["Access-Control-Allow-Origin"] = "*"
    return resp


def _oauth_error(error: str, description: str, status: int = 400) -> JsonResponse:
    return _json({"error": error, "error_description": description}, status)


# ---------------------------------------------------------------------------------------------- discovery
def _cacheable(resp):
    resp["Cache-Control"] = "public, max-age=300"
    resp["Access-Control-Allow-Origin"] = "*"
    return resp


@gated
@require_http_methods(["GET", "HEAD", "OPTIONS"])
def protected_resource(request, suffix=""):
    """RFC 9728."""
    _gate()
    if request.method == "OPTIONS":
        return _cors_preflight()
    doc = {
        "resource": addresses.resource(request),
        "authorization_servers": [addresses.issuer(request)],
        "scopes_supported": [s for s in SUPPORTED_SCOPES if s != sc.OFFLINE_ACCESS] + [sc.OFFLINE_ACCESS],
        "bearer_methods_supported": ["header"],
        "resource_name": _("Ravix MCP"),
    }
    return _cacheable(JsonResponse(doc))


@gated
@require_http_methods(["GET", "HEAD", "OPTIONS"])
def authorization_server(request, suffix=""):
    """RFC 8414."""
    _gate()
    if request.method == "OPTIONS":
        return _cors_preflight()
    doc = {
        "issuer": addresses.issuer(request),
        "authorization_endpoint": addresses.absolute(request, "mcp:authorize"),
        "token_endpoint": addresses.absolute(request, "mcp:token"),
        "registration_endpoint": addresses.absolute(request, "mcp:register"),
        "revocation_endpoint": addresses.absolute(request, "mcp:revoke"),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }
    return _cacheable(JsonResponse(doc))


def _cors_preflight():
    resp = HttpResponse(status=204)
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    resp["Access-Control-Allow-Headers"] = "Content-Type, MCP-Protocol-Version"
    resp["Access-Control-Max-Age"] = "600"
    return resp


# ---------------------------------------------------------------------------------------------- registration
@gated
@csrf_exempt
@require_http_methods(["POST"])
def register(request):
    """RFC 7591 for public clients."""
    _gate()
    if not ratelimit.allow_ip(request, "register", 20, 3600):
        return _oauth_error("temporarily_unavailable", "too many registrations from this address", 429)
    if int(request.META.get("CONTENT_LENGTH") or 0) > 16384:
        return _oauth_error("invalid_client_metadata", "the request is too large", 413)
    try:
        payload = json.loads(request.read(16385).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _oauth_error("invalid_client_metadata", "the body must be JSON")
    try:
        client = clients.register_dynamic(payload)
    except clients.ClientError as exc:
        return _oauth_error(exc.code if exc.code != "invalid_client" else "invalid_client_metadata", exc.message, 429 if exc.code == "temporarily_unavailable" else 400)
    return _json({
        "client_id": client.client_id, "client_id_issued_at": int(client.created_at.timestamp()), "client_name": client.name,
        "redirect_uris": client.redirect_uris, "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, 201)


# ---------------------------------------------------------------------------------------------- authorize + consent
def _error_page(request, message: str, status: int = 400):
    resp = render(request, "mcp/oauth_error.html", {"message": message}, status=status)
    resp["X-Frame-Options"] = "DENY"
    return _no_store(resp)


def _redirect_back(redirect_uri: str, request, **params) -> HttpResponse:
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(k, v) for k, v in params.items() if v not in (None, "")]
    query.append(("iss", addresses.issuer(request)))
    resp = HttpResponse(status=302)
    resp["Location"] = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return _no_store(resp)


class _Req:
    """The validated parts of an authorization request."""

    def __init__(self, client, redirect_uri, state, challenge, scopes, resource):
        self.client, self.redirect_uri, self.state, self.challenge, self.scopes, self.resource = client, redirect_uri, state, challenge, scopes, resource


def _parse_authorization_request(request, data):
    """Returns (_Req, None) or (None, error response). Until the redirect address is proven to belong to the app, errors are shown, never redirected."""
    client_id = data.get("client_id", "")
    try:
        client = clients.resolve_client(client_id)
    except clients.ClientError as exc:
        return None, _error_page(request, _("This app could not be identified, so it cannot be connected. (%(why)s)") % {"why": exc.message})
    redirect_uri = data.get("redirect_uri", "")
    if redirect_uri not in client.redirect_uris:
        return None, _error_page(request, _("The address this app wants to return to is not one it registered, so the connection was stopped."))
    state = data.get("state", "")[:512]

    def fail(error, description):
        return None, _redirect_back(redirect_uri, request, error=error, error_description=description, state=state)

    if data.get("response_type") != "code":
        return fail("unsupported_response_type", "only response_type=code is supported")
    challenge = data.get("code_challenge", "")
    if data.get("code_challenge_method") != "S256" or not tokens.valid_challenge(challenge):
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
    resource = data.get("resource") or addresses.resource(request)
    if not addresses.same_resource(resource, addresses.resource(request)):
        return fail("invalid_target", "resource must be the MCP endpoint of this site")
    asked = sc.parse(data.get("scope", "")) or [sc.SITE_READ]
    return _Req(client, redirect_uri, state, challenge, asked, addresses.resource(request)), None


def _consent_response(request, cfg, req: _Req, error: str = ""):
    user = request.user
    can = set(sc.grantable(user, cfg))
    rows = [{"scope": s, "label": sc.LABELS[s], "allowed": s in can} for s in req.scopes]
    usable = [r["scope"] for r in rows if r["allowed"]]
    hosts = req.client.redirect_hosts() or [clients.uri_host(req.redirect_uri)]
    trusted = clients.all_trusted(req.client, req.redirect_uri, cfg)
    blocked = (not trusted and cfg.unknown_apps == McpSettings.UnknownApps.TRUSTED_ONLY) or not usable
    ctx = {
        "client": req.client, "rows": rows, "usable": usable, "host": clients.uri_host(req.redirect_uri), "hosts": hosts, "trusted": trusted,
        "blocked_unknown": not trusted and cfg.unknown_apps == McpSettings.UnknownApps.TRUSTED_ONLY, "nothing_allowed": not usable, "blocked": blocked,
        "role_label": user.get_role_display(), "error": error, "params": {
            "client_id": req.client.client_id, "redirect_uri": req.redirect_uri, "state": req.state, "code_challenge": req.challenge,
            "code_challenge_method": "S256", "scope": sc.join(req.scopes), "resource": req.resource, "response_type": "code"},
    }
    resp = render(request, "mcp/consent.html", ctx)
    origin = "{}://{}".format(*urlsplit(req.redirect_uri)[:2]) if urlsplit(req.redirect_uri).netloc else ""
    # The browser checks where a form may lead, and after "Allow" it is sent on to the app's address: name that one address and no other.
    resp["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; "
                                      f"base-uri 'self'; form-action 'self' {origin}; frame-ancestors 'none'").replace("  ", " ")
    resp["X-Frame-Options"] = "DENY"
    resp["Referrer-Policy"] = "same-origin"  # not "no-referrer": that makes browsers send Origin: null on the form post, which CSRF protection rightly refuses
    return _no_store(resp)


@gated
@never_cache
@require_http_methods(["GET", "POST"])
def authorize(request):
    cfg = _gate()
    if not ratelimit.allow_ip(request, "authorize", 120, 600):
        return _error_page(request, _("Too many attempts. Please wait a few minutes."), 429)
    data = request.POST if request.method == "POST" else request.GET
    req, problem = _parse_authorization_request(request, data)
    if problem is not None:
        return problem
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    if request.method == "GET":
        return _consent_response(request, cfg, req)

    decision = request.POST.get("decision", "")
    if decision != "allow":
        log_event(request, AuditLog.Action.MCP, None, _("A person declined to connect the app “%(app)s”.") % {"app": req.client.name}, object_type=_("AI assistant"))
        return _redirect_back(req.redirect_uri, request, error="access_denied", state=req.state)
    usable = [s for s in req.scopes if s in sc.grantable(request.user, cfg)]
    trusted = clients.all_trusted(req.client, req.redirect_uri, cfg)
    if not usable or (not trusted and cfg.unknown_apps == McpSettings.UnknownApps.TRUSTED_ONLY):
        return _consent_response(request, cfg, req)
    if not trusted and request.POST.get("trust_host") != clients.uri_host(req.redirect_uri):
        return _consent_response(request, cfg, req, error=_("Please confirm that you trust this address, or choose Deny."))
    code = tokens.new("code")
    AuthorizationCode.objects.create(
        code_hash=tokens.digest(code), client=req.client, user=request.user, redirect_uri=req.redirect_uri, code_challenge=req.challenge,
        scope=sc.join(usable), resource=req.resource, expires_at=timezone.now() + timedelta(seconds=CODE_SECONDS),
    )
    log_event(request, AuditLog.Action.MCP, None, _("A person allowed the app “%(app)s” (%(host)s) to connect with: %(scopes)s.")
              % {"app": req.client.name, "host": clients.uri_host(req.redirect_uri), "scopes": sc.join(usable)}, object_type=_("AI assistant"))
    return _redirect_back(req.redirect_uri, request, code=code, state=req.state)


# ---------------------------------------------------------------------------------------------- token + revoke
def _issue(connection: McpConnection, cfg: McpSettings, with_refresh: bool) -> dict:
    now = timezone.now()
    access = tokens.new("access")
    McpToken.objects.create(connection=connection, kind=McpToken.ACCESS, token_hash=tokens.digest(access), expires_at=now + cfg.access_ttl)
    body = {"access_token": access, "token_type": "Bearer", "expires_in": int(cfg.access_ttl.total_seconds()), "scope": connection.scope}
    if with_refresh:
        refresh = tokens.new("refresh")
        McpToken.objects.create(connection=connection, kind=McpToken.REFRESH, token_hash=tokens.digest(refresh), expires_at=now + timedelta(days=REFRESH_DAYS))
        body["refresh_token"] = refresh
    return body


def _grant_code(request, cfg, form):
    code_value = form.get("code", "")
    with transaction.atomic():
        row = AuthorizationCode.objects.select_for_update().select_related("client", "user").filter(code_hash=tokens.digest(code_value)).first()
        if row is None:
            return _oauth_error("invalid_grant", "unknown or expired code")
        if row.used_at is not None:
            # A code is single-use. Seeing it again means somebody else may hold it: kill whatever it produced.
            if row.connection_id:
                row.connection.revoke("code_reuse")
                log_event(request, AuditLog.Action.MCP, None, _("An authorization code was used twice; the connection of “%(app)s” was ended.") % {"app": row.client.name},
                          object_type=_("AI assistant"), actor=row.user)
            return _oauth_error("invalid_grant", "the code was already used")
        if row.expires_at < timezone.now():
            return _oauth_error("invalid_grant", "unknown or expired code")
        if form.get("client_id") != row.client.client_id:
            return _oauth_error("invalid_grant", "the code was issued to a different client")
        if form.get("redirect_uri") != row.redirect_uri:
            return _oauth_error("invalid_grant", "redirect_uri does not match the authorization request")
        if not tokens.pkce_ok(form.get("code_verifier", ""), row.code_challenge):
            return _oauth_error("invalid_grant", "PKCE verification failed")
        if form.get("resource") and not addresses.same_resource(form["resource"], row.resource):
            return _oauth_error("invalid_target", "resource does not match the authorization request")
        user = row.user
        granted = sc.effective(user, sc.parse(row.scope), cfg)
        if not user.is_active or not granted or getattr(user, "must_change_password", False):
            return _oauth_error("invalid_grant", "this person can no longer grant access")
        row.used_at = timezone.now()
        connection = McpConnection.objects.create(user=user, client=row.client, scope=sc.join(granted), resource=row.resource)
        row.connection = connection
        row.save(update_fields=["used_at", "connection"])
        # one live connection per person and app: a new approval replaces the old one
        for old in McpConnection.objects.filter(user=user, client=row.client, revoked_at__isnull=True).exclude(pk=connection.pk):
            old.revoke("replaced")
        body = _issue(connection, cfg, with_refresh=sc.OFFLINE_ACCESS in granted)
        body["scope"] = connection.scope
    return _json(body)


def _grant_refresh(request, cfg, form):
    value = form.get("refresh_token", "")
    with transaction.atomic():
        row = McpToken.objects.select_for_update().select_related("connection__client", "connection__user").filter(token_hash=tokens.digest(value), kind=McpToken.REFRESH).first()
        if row is None:
            return _oauth_error("invalid_grant", "unknown refresh token")
        connection = row.connection
        if form.get("client_id") != connection.client.client_id:
            return _oauth_error("invalid_grant", "the token was issued to a different client")
        if row.used_at is not None:
            connection.revoke("refresh_reuse")
            log_event(request, AuditLog.Action.MCP, None, _("A refresh token was used twice; the connection of “%(app)s” was ended.") % {"app": connection.client.name},
                      object_type=_("AI assistant"), actor=connection.user)
            return _oauth_error("invalid_grant", "the refresh token was already used")
        if row.expires_at < timezone.now() or not connection.active:
            return _oauth_error("invalid_grant", "the connection has expired or was revoked")
        user = connection.user
        if not user.is_active or getattr(user, "must_change_password", False):
            connection.revoke("user_unavailable")
            return _oauth_error("invalid_grant", "this person can no longer grant access")
        if user.password_changed_at and user.password_changed_at > connection.created_at:
            connection.revoke("password_changed")
            return _oauth_error("invalid_grant", "the password was changed; connect again")
        asked = sc.parse(form.get("scope", ""))
        if asked and not set(asked) <= set(connection.scope_list):
            return _oauth_error("invalid_scope", "a refresh cannot widen the granted scopes")
        if asked:
            connection.scope = sc.join(asked)
            connection.save(update_fields=["scope"])
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        connection.tokens.filter(kind=McpToken.ACCESS).delete()
        body = _issue(connection, cfg, with_refresh=True)
    return _json(body)


@gated
@csrf_exempt
@require_http_methods(["POST"])
def token(request):
    cfg = _gate()
    if not ratelimit.allow_ip(request, "token", 240, 600):
        return _oauth_error("temporarily_unavailable", "too many requests", 429)
    form = request.POST
    grant = form.get("grant_type", "")
    if grant == "authorization_code":
        return _grant_code(request, cfg, form)
    if grant == "refresh_token":
        return _grant_refresh(request, cfg, form)
    return _oauth_error("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")


@gated
@csrf_exempt
@require_http_methods(["POST"])
def revoke(request):
    """RFC 7009. Always 200, so the answer reveals nothing about whether a token existed."""
    _gate()
    if not ratelimit.allow_ip(request, "revoke", 240, 600):
        return _oauth_error("temporarily_unavailable", "too many requests", 429)
    value = request.POST.get("token", "")
    row = McpToken.objects.select_related("connection__client", "connection__user").filter(token_hash=tokens.digest(value)).first() if value else None
    if row is not None and request.POST.get("client_id") == row.connection.client.client_id:
        connection = row.connection
        if row.kind == McpToken.REFRESH:
            connection.revoke("revoked_by_app")
            log_event(request, AuditLog.Action.MCP, None, _("The app “%(app)s” ended its own connection.") % {"app": connection.client.name}, object_type=_("AI assistant"), actor=connection.user)
        else:
            row.delete()
    return _no_store(HttpResponse(status=200))
