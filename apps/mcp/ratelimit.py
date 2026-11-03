"""Rate limits that need no Redis: the same cache (with its database fallback) that protects sign-in."""
from __future__ import annotations

import time

from apps.accounts import throttle


def allow_ip(request, kind: str, limit: int, window: int = 3600) -> bool:
    return throttle.allow_action(request, f"mcp-{kind}", limit, window)


def allow_connection(connection_id: int, bucket: str, limit: int, window: int = 60) -> tuple[bool, int]:
    """A fixed window per connection. Returns (allowed, seconds until the window resets)."""
    slot = int(time.time() // window)
    key = f"mcp-rate:{bucket}:{connection_id}:{slot}"
    count = throttle._with_cache(lambda c: throttle._bump_on(c, key, window + 5))
    return count <= limit, window - int(time.time() % window)
