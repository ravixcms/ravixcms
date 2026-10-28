#!/usr/bin/env python3
"""A tiny stand-in for Passenger / LiteSpeed (LSAPI) used by CI and local checks. It serves ``passenger_wsgi.application`` and, like both of them,
starts a fresh application process when ``tmp/restart.txt`` is touched.

    python scripts/passenger_sim.py --port 8765 [--mount /site] [--https]

``--mount`` behaves like cPanel's "Application URL": the prefix is moved from PATH_INFO to SCRIPT_NAME. ``--https`` sets HTTPS=on like the web server would.
"""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # --root points at another application folder (the unpacked cPanel package)


def serve(port, mount, https):
    global ROOT
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import passenger_wsgi

    class Threaded(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if mount:
            if path != mount and not path.startswith(mount + "/"):
                start_response("404 Not Found", [("Content-Type", "text/plain")])
                return [b"outside the application URL"]
            environ["SCRIPT_NAME"] = mount
            environ["PATH_INFO"] = path[len(mount):] or "/"
        if https:
            environ["HTTPS"] = "on"
        return passenger_wsgi.application(environ, start_response)

    make_server("127.0.0.1", port, app, server_class=Threaded, handler_class=Quiet).serve_forever()


def supervise(port, mount, https):
    flag = ROOT / "tmp" / "restart.txt"
    flag.parent.mkdir(exist_ok=True)

    def stamp():
        try:
            return flag.stat().st_mtime_ns
        except OSError:
            return 0

    def spawn():
        argv = [sys.executable, str(Path(__file__).resolve()), "--serve", "--port", str(port), "--root", str(ROOT)] + (["--mount", mount] if mount else []) + (["--https"] if https else [])
        return subprocess.Popen(argv, cwd=str(ROOT))

    child, seen = spawn(), stamp()
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))
    try:
        while not stop:
            time.sleep(0.25)
            now = stamp()
            if now != seen or child.poll() is not None:
                seen = now
                child.terminate()
                try:
                    child.wait(5)
                except subprocess.TimeoutExpired:
                    child.kill()
                child = spawn()
                print("[sim] application restarted", flush=True)
    finally:
        child.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mount", default="")
    ap.add_argument("--https", action="store_true")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--root", default="")
    a = ap.parse_args()
    if a.root:
        ROOT = Path(a.root).resolve()
    mount = ("/" + a.mount.strip("/")) if a.mount.strip("/") else ""
    (serve if a.serve else supervise)(a.port, mount, a.https)
