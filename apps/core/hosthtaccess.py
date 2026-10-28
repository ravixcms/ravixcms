"""The ``.htaccess`` files for shared hosting. They use only directives that both Apache and LiteSpeed understand, each guarded the way
WordPress hardening snippets are, so the same text works on either server (and is harmless where .htaccess is ignored: the real protection is that
private files are never placed inside the web root in the first place; these files are a second layer)."""
from __future__ import annotations

import os
from pathlib import Path

MARK = "# Ravix (managed file: it is rewritten by Ravix; put your own rules in another .htaccess)"

_DENY = """<IfModule mod_authz_core.c>
    Require all denied
</IfModule>
<IfModule !mod_authz_core.c>
    Order deny,allow
    Deny from all
</IfModule>
"""

_DENY_FILES = r"""<FilesMatch "(?i)\.(env|ini|sqlite3?|db|sql|bak|backup|log|key|pem|zip|tar|gz|tgz|lock|marker|py|pyc|toml|cfg|sh|po|mo|json\.gz)$">
""" + "\n".join("    " + line for line in _DENY.splitlines()) + """
</FilesMatch>
"""

#: In the application folder (in case it sits inside public_html): nothing but the web server's own static files may be fetched.
APP_ROOT = f"""{MARK}
Options -Indexes
{_DENY_FILES}"""

#: Everything inside the private data folder is for Ravix only.
DATA = f"""{MARK}
{_DENY}"""

#: Uploaded media: served as plain files, never executed, never listed, never sniffed into another type.
MEDIA = rf"""{MARK}
Options -Indexes -ExecCGI -Includes
<FilesMatch "(?i)\.(php[0-9]?|phtml|phar|pl|py|pyc|cgi|fcgi|sh|bash|asp|aspx|jsp|exe|dll|htaccess|htpasswd|ini|env)$">
{chr(10).join("    " + line for line in _DENY.splitlines())}
</FilesMatch>
<IfModule mod_mime.c>
    RemoveHandler .php .phtml .php3 .php4 .php5 .php7 .phar .pl .py .cgi .fcgi
    RemoveType .php .phtml .php3 .php4 .php5 .php7 .phar .pl .py .cgi
    AddType text/plain .php .phtml .pl .py .cgi .sh
</IfModule>
<IfModule mod_headers.c>
    Header set X-Content-Type-Options "nosniff"
    Header set Content-Security-Policy "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; sandbox"
</IfModule>
"""


def _write(path: Path, text: str) -> bool:
    """Create or refresh a file we manage. A file without our mark was written by the owner: leave it alone."""
    try:
        if path.exists() and MARK not in path.read_text(encoding="utf-8", errors="replace"):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        if os.name == "posix":
            os.chmod(path, 0o644)  # the web server must be able to read it
        return True
    except OSError:
        return False


def ensure(media_root: Path | None = None, data_dir: Path | None = None, app_root: Path | None = None) -> dict[str, bool]:
    """Write the managed .htaccess files that exist on this host. Safe to call any time."""
    from django.conf import settings

    from . import hosting

    media_root = Path(media_root or settings.MEDIA_ROOT)
    out = {"media": _write(media_root / ".htaccess", MEDIA), "data": _write(Path(data_dir or hosting.data_dir()) / ".htaccess", DATA)}
    if (app_root or hosting.APP_ROOT) and (Path(app_root or hosting.APP_ROOT) / "ravix-hosting.marker").exists():
        out["app"] = _write(Path(app_root or hosting.APP_ROOT) / ".htaccess", APP_ROOT)
    return out
