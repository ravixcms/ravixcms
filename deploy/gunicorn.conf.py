"""تنظیمات Gunicorn (فایل را با -c به gunicorn بدهید). مقادیر با متغیر محیطی قابل تغییرند."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "unix:/run/ravix/gunicorn.sock")
workers = int(os.environ.get("GUNICORN_WORKERS", max(2, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
timeout = 30
graceful_timeout = 30
keepalive = 5
max_requests = 1000          # بازیابی دوره‌ای ورکر (نشت حافظه)
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
loglevel = (os.environ.get("RAVIX_LOG_LEVEL") or os.environ.get("RAVIN_LOG_LEVEL") or "info").lower()
umask = 0o007                # سوکت فقط برای کاربر و گروه (nginx عضو گروه www-data است)
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
