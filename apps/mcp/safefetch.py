"""Fetching a URL that somebody else chose, without letting them aim the server at its own network.

Used for Client ID Metadata Documents and (later) for "upload media from a URL". Rules: HTTPS only; the host name is resolved once, every
address must be a public one, and the connection goes to THAT address (so a DNS answer that changes between the check and the connect, the
"rebinding" trick, gains nothing); no redirects; a hard size cap; a short timeout.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 5
PORTS = (443,)


class FetchError(Exception):
    """The address is refused or could not be read. The message is safe to show to an administrator."""


def public_address(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_multicast or addr.is_reserved or addr.is_loopback or addr.is_link_local or addr.is_private)


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("host not found") from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


class _PinnedHTTPS(http.client.HTTPSConnection):
    """Connects to a chosen IP but verifies the certificate for, and sends, the original host name."""

    def __init__(self, host: str, ip: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _open(host: str, ip: str, port: int, timeout: float):  # a seam for tests
    return _PinnedHTTPS(host, ip, port, timeout)


def validate_url(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        raise FetchError("only https addresses are allowed")
    if parts.username or parts.password:
        raise FetchError("addresses with a user name are not allowed")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise FetchError("bad port") from exc
    if port not in PORTS:
        raise FetchError("only the standard https port is allowed")
    host = parts.hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        raise FetchError("an IP address is not allowed here")
    except ValueError:
        pass
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, port, path


def fetch(url: str, *, max_bytes: int = 5120, accept: str = "application/json", timeout: float = DEFAULT_TIMEOUT) -> tuple[bytes, str]:
    """Returns (body, content type). Raises FetchError for anything unsafe, slow, big or not a plain 200."""
    host, port, path = validate_url(url)
    addresses = resolve(host, port)
    if not addresses or not all(public_address(a) for a in addresses):
        raise FetchError("the address points to a private or reserved network")
    conn = _open(host, addresses[0], port, timeout)
    try:
        conn.request("GET", path, headers={"Host": host, "Accept": accept, "User-Agent": "Ravix-MCP", "Connection": "close"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise FetchError(f"the server answered {resp.status}")
        declared = resp.getheader("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError("the document is too large")
        body = resp.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise FetchError("the document is too large")
        return body, (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
    except FetchError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise FetchError("could not be read") from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
