"""Who is asking to connect: Client ID Metadata Documents (the client_id is an https URL) and Dynamic Client Registration."""
from __future__ import annotations

import ipaddress
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.utils import timezone

from . import safefetch, tokens
from .models import OAuthClient

KNOWN_HOSTS = ("claude.ai", "claude.com")  # shown as "verified"; everybody else gets the address in red
MAX_REDIRECTS = 5
MAX_UNUSED_CLIENTS = 500
CIMD_REFRESH = timedelta(hours=1)
LOOPBACK = {"localhost", "127.0.0.1", "[::1]", "::1"}
BAD_SCHEMES = {"javascript", "data", "file", "vbscript", "blob", "about", "ftp", "http", "https", "ws", "wss", "chrome", "view-source"}


class ClientError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def uri_host(uri: str) -> str:
    try:
        parts = urlsplit(uri)
    except ValueError:
        return ""
    return (parts.hostname or parts.scheme or "").lower()


def valid_redirect_uri(uri: str) -> bool:
    """https; or http to the machine itself (native apps); or a private-use scheme such as com.example.app:/cb. No fragment, no credentials."""
    if not isinstance(uri, str) or not uri or len(uri) > 2048 or re.search(r"[\s\x00-\x1f]", uri):
        return False
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or "#" in uri or parts.username or parts.password:
        return False
    scheme = parts.scheme.lower()
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":
        host = (parts.hostname or "").lower()
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    return bool(re.fullmatch(r"[a-z][a-z0-9+.\-]*", scheme)) and scheme not in BAD_SCHEMES and ("." in scheme or "-" in scheme)


def _clean_name(value) -> str:
    text = re.sub(r"[\x00-\x1f\x7f<>]", "", str(value or "")).strip()
    return text[:120] or "App"


def _clean_url(value) -> str:
    value = str(value or "").strip()
    return value[:500] if value.startswith("https://") else ""


def _redirects(value) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > MAX_REDIRECTS:
        raise ClientError("invalid_redirect_uri", "redirect_uris must be a short list")
    if not all(valid_redirect_uri(u) for u in value):
        raise ClientError("invalid_redirect_uri", "a redirect address is not acceptable")
    return list(dict.fromkeys(value))


def register_dynamic(payload) -> OAuthClient:
    """RFC 7591 for public clients only (token_endpoint_auth_method "none")."""
    if not isinstance(payload, dict):
        raise ClientError("invalid_client_metadata", "expected a JSON object")
    method = payload.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise ClientError("invalid_client_metadata", "only public clients (token_endpoint_auth_method none) are supported")
    grants = payload.get("grant_types", ["authorization_code"])
    if not isinstance(grants, list) or not set(grants) <= {"authorization_code", "refresh_token"}:
        raise ClientError("invalid_client_metadata", "unsupported grant_types")
    if payload.get("response_types", ["code"]) != ["code"]:
        raise ClientError("invalid_client_metadata", "only response_type code is supported")
    redirects = _redirects(payload.get("redirect_uris"))
    cutoff = timezone.now() - timedelta(days=7)
    OAuthClient.objects.filter(kind=OAuthClient.Kind.DCR, created_at__lt=cutoff, connections__isnull=True).delete()
    if OAuthClient.objects.filter(kind=OAuthClient.Kind.DCR, connections__isnull=True).count() >= MAX_UNUSED_CLIENTS:
        raise ClientError("temporarily_unavailable", "too many unused registrations; try again later")
    return OAuthClient.objects.create(
        client_id=tokens.new("client"), kind=OAuthClient.Kind.DCR, name=_clean_name(payload.get("client_name")),
        client_uri=_clean_url(payload.get("client_uri")), logo_uri=_clean_url(payload.get("logo_uri")), redirect_uris=redirects, fetched_at=None,
    )


def is_cimd_id(client_id: str) -> bool:
    try:
        parts = urlsplit(client_id or "")
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname) and parts.path not in ("", "/") and not parts.fragment and not parts.query


def _fetch_document(client_id: str) -> dict:
    try:
        body, _ctype = safefetch.fetch(client_id, max_bytes=5120)
        doc = json.loads(body.decode("utf-8"))
    except safefetch.FetchError as exc:
        raise ClientError("invalid_client", f"the client metadata document could not be read: {exc}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise ClientError("invalid_client", "the client metadata document is not valid JSON") from exc
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise ClientError("invalid_client", "client_id in the document must equal the document's address")
    if doc.get("token_endpoint_auth_method", "none") != "none":
        raise ClientError("invalid_client", "only public clients are supported")
    return doc


def resolve_client(client_id: str) -> OAuthClient:
    """The client for a client_id: a stored one, or (for an https URL) its metadata document, read now or refreshed when stale."""
    client_id = (client_id or "").strip()
    if not client_id or len(client_id) > 500:
        raise ClientError("invalid_client", "unknown client")
    existing = OAuthClient.objects.filter(client_id=client_id).first()
    if existing and existing.kind == OAuthClient.Kind.DCR:
        return existing
    if not is_cimd_id(client_id):
        raise ClientError("invalid_client", "unknown client")
    if existing and existing.fetched_at and timezone.now() - existing.fetched_at < CIMD_REFRESH:
        return existing
    try:
        doc = _fetch_document(client_id)
        redirects = _redirects(doc.get("redirect_uris"))
    except ClientError:
        if existing:  # keep working with what we last read rather than lock the person out because the app's server hiccuped
            return existing
        raise
    values = dict(name=_clean_name(doc.get("client_name")), client_uri=_clean_url(doc.get("client_uri")), logo_uri=_clean_url(doc.get("logo_uri")),
                  redirect_uris=redirects, fetched_at=timezone.now())
    client, _ = OAuthClient.objects.update_or_create(client_id=client_id, defaults={**values, "kind": OAuthClient.Kind.CIMD})
    return client


def is_trusted_host(host: str, cfg) -> bool:
    host = (host or "").lower()
    return host in KNOWN_HOSTS or host in cfg.trusted_host_list()


def all_trusted(client: OAuthClient, redirect_uri: str, cfg) -> bool:
    return is_trusted_host(uri_host(redirect_uri), cfg)
