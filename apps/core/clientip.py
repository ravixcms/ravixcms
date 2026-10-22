"""تنها نقطهٔ تعیین IP کلاینت؛ هر جای دیگری که IP می‌خواهد باید از ``client_ip`` استفاده کند.

قاعده:
* ``REMOTE_ADDR`` همیشه واقعی است (TCP). سربرگ‌های ``X-Forwarded-For`` / ``X-Real-IP`` را هر کلاینتی می‌تواند بفرستد،
  پس فقط وقتی خوانده می‌شوند که درخواست از یک **پروکسی مورداعتماد** آمده باشد:
  سوکت یونیکس (Gunicorn پشت Nginx روی سوکت، ``REMOTE_ADDR`` خالی است) یا نشانی/شبکه‌ای در ``RAVIX_TRUSTED_PROXIES``.
* در زنجیرهٔ ``X-Forwarded-For`` از **راست** می‌رویم و اولین نشانی غیرمورداعتماد را برمی‌داریم (چپ‌ترین‌ها را مهاجم می‌نویسد).
* اگر IP قابل‌تشخیص نبود ``None`` برمی‌گردد؛ تماس‌گیرنده نباید یک «IP مشترک» بسازد (محدودیت سراسری IP اعمال نمی‌شود).
"""
from __future__ import annotations

import ipaddress
import logging
import time
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_ATTR = "_ravix_client_ip"
OBS_TTL = 24 * 3600
OBS_KEYS = ("untrusted_proxy", "no_headers")


@lru_cache(maxsize=8)
def _parse_networks(raw: tuple[str, ...]):
    nets, bad = [], []
    for item in raw:
        try:
            nets.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            bad.append(item)
    return tuple(nets), tuple(bad)


def trusted_networks():
    return _parse_networks(tuple(getattr(settings, "RAVIX_TRUSTED_PROXIES", ()) or ()))[0]


def invalid_trusted_entries() -> list[str]:
    return list(_parse_networks(tuple(getattr(settings, "RAVIX_TRUSTED_PROXIES", ()) or ()))[1])


def _addr(raw: str):
    try:
        return ipaddress.ip_address((raw or "").strip())
    except ValueError:
        return None


def _is_trusted(ip) -> bool:
    return any(ip in net for net in trusted_networks())


def _is_unix_socket(remote: str) -> bool:
    return remote == "" or remote.startswith("/") or remote == "unix"


def _chain(meta) -> list[str]:
    xff = meta.get("HTTP_X_FORWARDED_FOR", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts and meta.get("HTTP_X_REAL_IP", "").strip():
        parts = [meta["HTTP_X_REAL_IP"].strip()]
    return parts


def _resolve(meta) -> tuple[str | None, str]:
    """(ip یا None، وضعیت): direct | proxied | untrusted_proxy | no_headers | bad_header."""
    remote = (meta.get("REMOTE_ADDR") or "").strip()
    remote_ip = _addr(remote)
    from_unix = _is_unix_socket(remote)
    proxy_trusted = from_unix or (remote_ip is not None and _is_trusted(remote_ip))
    chain = _chain(meta)
    if not proxy_trusted:
        if remote_ip is None:
            return None, "bad_header"
        private = remote_ip.is_private or remote_ip.is_loopback or remote_ip.is_link_local
        return str(remote_ip), ("untrusted_proxy" if (chain and private) else "direct")
    if not chain:
        return None, "no_headers"
    for raw in reversed(chain):
        ip = _addr(raw)
        if ip is None:
            return None, "bad_header"  # ردیف خراب: به حدس زدن تن نمی‌دهیم
        if not _is_trusted(ip):
            return str(ip), "proxied"
    first = _addr(chain[0])
    return (str(first) if first else None), "proxied"  # همهٔ زنجیره مورداعتماد (کلاینت داخلی)


def client_ip(request) -> str | None:
    cached = getattr(request, _ATTR, False)
    if cached is not False:
        return cached
    ip, state = _resolve(request.META)
    if state in OBS_KEYS:
        _observe(state, (request.META.get("REMOTE_ADDR") or "unix socket"))
    setattr(request, _ATTR, ip)
    return ip


def diagnose(request) -> tuple[str | None, str]:
    """برای «سلامت سایت»: IP و وضعیت درخواست جاری."""
    return _resolve(request.META)


def _observe(state: str, remote: str) -> None:
    try:
        cache.add(f"clientip:obs:{state}", {"remote": remote[:64], "at": time.time()}, OBS_TTL)
    except Exception:  # noqa: BLE001 — ثبت مشاهده هرگز نباید درخواست را بشکند
        logger.debug("ثبت مشاهدهٔ IP ناموفق بود", exc_info=True)


def observations() -> dict[str, dict]:
    out = {}
    for state in OBS_KEYS:
        try:
            v = cache.get(f"clientip:obs:{state}")
        except Exception:  # noqa: BLE001
            v = None
        if v:
            out[state] = v
    return out
