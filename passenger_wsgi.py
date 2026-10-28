"""WSGI entry point for shared hosting: cPanel "Setup Python App" (Passenger) and LiteSpeed (LSAPI) both load ``application`` from this file.

Set "Application startup file" to ``passenger_wsgi.py`` and "Application entry point" to ``application`` (the defaults).

* Chooses the shared-hosting settings (``config.settings.hosting``) and loads the private env file from the data folder.
* Turns the server's own HTTPS indicators into ``wsgi.url_scheme`` (no forwarded header is trusted).
* When the site lives in a sub-folder (example.com/site/) and the server hands over the full path, moves the prefix from PATH_INFO to SCRIPT_NAME
  (``RAVIX_SCRIPT_NAME``; Passenger normally sets SCRIPT_NAME itself and nothing needs to change).
* If Ravix cannot start, shows a short, safe page and writes the reason to ``<data folder>/logs/startup-error.txt`` instead of a bare 503.
Restart after changes: Passenger and LiteSpeed both reload when ``tmp/restart.txt`` is touched (the "Restart" button in Setup Python App does that).
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.hosting")


def _script_name():
    name = os.environ.get("RAVIX_SCRIPT_NAME", "").strip()
    if not name:
        try:
            from apps.core import hosting

            name = hosting.read_env_file().get("RAVIX_SCRIPT_NAME", "").strip()
        except Exception:  # noqa: BLE001
            name = ""
    return "/" + name.strip("/") if name.strip("/") else ""


def _fix_environ(environ, script_name):
    https = str(environ.get("HTTPS", "")).lower() in {"on", "1", "true"} or environ.get("REQUEST_SCHEME") == "https" or environ.get("SSL_PROTOCOL")
    if https:
        environ["wsgi.url_scheme"] = "https"
    path = environ.get("PATH_INFO", "")
    if script_name and not environ.get("SCRIPT_NAME", "").startswith(script_name) and (path == script_name or path.startswith(script_name + "/")):
        environ["SCRIPT_NAME"] = script_name
        environ["PATH_INFO"] = path[len(script_name):] or "/"
    return environ


def _startup_error_app(exc):
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    where = ""
    try:
        from apps.core import hosting

        logs = hosting.ensure_dir(hosting.data_dir() / "logs")
        target = logs / "startup-error.txt"
        target.write_text(detail, encoding="utf-8")
        os.chmod(target, 0o600)
        where = str(target)
    except Exception:  # noqa: BLE001
        sys.stderr.write(detail)

    def app(environ, start_response):
        body = ("Ravix could not start.\n\n"
                "The reason was saved to the file below (open it from cPanel File Manager):\n" + (where or "(the Python app's stderr log)") + "\n\n"
                "Common causes: dependencies not installed (run \"Run Pip Install\" in Setup Python App), a missing or wrong settings file, "
                "or an unsupported Python version (Ravix needs Python 3.11 or newer).\n").encode("utf-8")
        start_response("503 Service Unavailable", [("Content-Type", "text/plain; charset=utf-8"), ("Cache-Control", "no-store"), ("Content-Length", str(len(body)))])
        return [body]

    return app


try:
    if sys.version_info < (3, 11):
        raise RuntimeError("Ravix needs Python 3.11 or newer; this application runs Python %d.%d. Pick a newer version in Setup Python App." % sys.version_info[:2])
    from django.core.wsgi import get_wsgi_application

    _django = get_wsgi_application()
    _prefix = _script_name()

    def application(environ, start_response):
        return _django(_fix_environ(environ, _prefix), start_response)
except Exception as exc:  # noqa: BLE001
    application = _startup_error_app(exc)
