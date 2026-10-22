"""مقایسهٔ نسخه‌ی معنایی ساده (major.minor.patch) و بازه‌های سازگاری مثل ``>=0.5,<1.0``."""
import re

_VERSION = re.compile(r"^(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?$")
_CLAUSE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d{1,4}\.\d{1,4}(?:\.\d{1,4})?)$")
_OPS = {
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b, ">": lambda a, b: a > b, "<": lambda a, b: a < b,
}


def parse(version: str):
    m = _VERSION.match((version or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def is_valid_version(version: str) -> bool:
    """نسخه‌ی افزونه: دقیقاً سه بخش عددی، مثل 1.0.0."""
    return bool(re.match(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$", version or ""))


def clauses(spec: str):
    """بازه‌ی نسخه را به فهرست (عملگر، نسخه) تبدیل می‌کند؛ بازه‌ی نامعتبر ← None. بازه‌ی خالی ← فهرست خالی (هر نسخه‌ای)."""
    spec = (spec or "").strip()
    if not spec:
        return []
    out = []
    for part in spec.split(","):
        m = _CLAUSE.match(part.strip())
        if not m:
            return None
        out.append((m.group(1), parse(m.group(2))))
    return out


def is_valid_spec(spec: str) -> bool:
    return clauses(spec) is not None


def satisfies(version: str, spec: str) -> bool:
    cl = clauses(spec)
    v = parse(version)
    if cl is None or v is None:
        return False
    return all(_OPS[op](v, ref) for op, ref in cl)
