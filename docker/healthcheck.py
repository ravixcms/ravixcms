"""سلامت کانتینر: GET /healthz روی خودِ gunicorn. هدر X-Forwarded-Proto مانع ریدایرکت SECURE_SSL_REDIRECT می‌شود."""
import sys
import urllib.request

req = urllib.request.Request("http://127.0.0.1:8000/healthz", headers={"Host": "localhost", "X-Forwarded-Proto": "https"})
try:
    with urllib.request.urlopen(req, timeout=4) as resp:  # noqa: S310
        sys.exit(0 if resp.status == 200 else 1)
except Exception:  # noqa: BLE001
    sys.exit(1)
