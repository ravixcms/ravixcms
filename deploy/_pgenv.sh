# منبع‌شدنی: DATABASE_URL را به متغیرهای PG* تبدیل می‌کند تا رمز دیتابیس در آرگومان پروسه‌ها (ps) دیده نشود.
eval "$(python3 - <<'PY'
import os, shlex
from urllib.parse import urlparse, unquote
u = urlparse(os.environ["DATABASE_URL"])
if u.scheme not in ("postgres", "postgresql", "pgsql"):
    raise SystemExit("echo 'DATABASE_URL باید PostgreSQL باشد' >&2; exit 1")
for k, v in (("PGHOST", u.hostname or "localhost"), ("PGPORT", str(u.port or 5432)), ("PGUSER", unquote(u.username or "")),
             ("PGPASSWORD", unquote(u.password or "")), ("PGDATABASE", u.path.lstrip("/"))):
    print(f"export {k}={shlex.quote(v)}")
PY
)"
unset DATABASE_URL
