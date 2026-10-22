"""خواندن امن پارامترهای عددی درخواست (query/POST): هرگز استثنا نمی‌دهد، رقم‌های غیر ASCII (مثل «²» یا «۱۲») و اعداد بزرگ‌تر از بازهٔ
دیتابیس را نادیده می‌گیرد (به‌جای خطای ۵۰۰ OverflowError/DataError)."""
from __future__ import annotations

MAX_ID = 2**31 - 1  # بازهٔ امن INTEGER در PostgreSQL و SQLite


def safe_int(raw, default: int | None = None, *, lo: int = 0, hi: int = MAX_ID) -> int | None:
    """عدد صحیح غیرمنفی ASCII در [lo, hi] یا ``default``."""
    s = raw.strip() if isinstance(raw, str) else ""
    if not s or len(s) > 12 or not (s.isascii() and s.isdigit()):
        return default
    n = int(s)
    return n if lo <= n <= hi else default


def safe_id(raw) -> int | None:
    """شناسهٔ مثبت معتبر (۱ تا MAX_ID) یا None."""
    return safe_int(raw, None, lo=1)


def safe_ids(values, limit: int = 200) -> list[int]:
    out = []
    for v in list(values)[:limit]:
        n = safe_id(v)
        if n is not None:
            out.append(n)
    return out
