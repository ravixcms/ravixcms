"""کلاینت HTTP امن برای افزونه‌های درگاه: فقط https، بدون دنبال‌کردن redirect، timeout و سقف حجم، بدون اتصال به شبکهٔ خصوصی.
خطاها به GatewayNetworkError (موقت) / GatewayRejected (رد صریح) نگاشت می‌شوند و هرگز بدنهٔ پاسخ خام یا هدرها را در پیام نمی‌گذارند."""
from django.utils.translation import gettext
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .base import GatewayError, GatewayNetworkError, GatewayRejected

MAX_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 10


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def _check_host(host: str):
    if getattr(settings, "SHOP_GATEWAY_ALLOW_PRIVATE_HOSTS", False):
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise GatewayNetworkError(gettext("The gateway host name was not found.")) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise GatewayError(gettext("The gateway address points to a private network and is not accepted."))


def _send(method, url, body, headers, timeout):
    """تنها نقطهٔ I/O واقعی (در تست‌ها جایگزین می‌شود). ← (status_code, bytes)"""
    parsed = urllib.parse.urlsplit(url)
    _check_host(parsed.hostname or "")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_BYTES + 1)
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        raise GatewayNetworkError(gettext("Could not connect to the gateway.")) from exc


def request_json(method: str, url: str, *, json_body=None, headers=None, timeout=DEFAULT_TIMEOUT) -> dict:
    """درخواست JSON. خروجی: dict پاسخ. وضعیت ۵xx/۴۲۹ ← GatewayNetworkError ؛ ۴xx ← GatewayRejected."""
    if not url.lower().startswith("https://"):
        raise GatewayError(gettext("Only https addresses are allowed for the gateway."))
    hdrs = {"Accept": "application/json", "User-Agent": "Ravix-Shop/1", **(headers or {})}
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    status, raw = _send(method.upper(), url, body, hdrs, timeout)
    if len(raw) > MAX_BYTES:
        raise GatewayError(gettext("The gateway response is too large."))
    if status >= 500 or status == 429:
        raise GatewayNetworkError((gettext("The gateway service is temporarily unavailable (code %(status)s).") % {"status": status}))
    if status >= 400:
        raise GatewayRejected((gettext("The gateway rejected the request (code %(status)s).") % {"status": status}))
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise GatewayError(gettext("The gateway response is not valid JSON.")) from exc
    if not isinstance(data, dict):
        raise GatewayError(gettext("The gateway response structure is invalid."))
    return data
