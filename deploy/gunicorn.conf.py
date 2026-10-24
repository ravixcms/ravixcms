"""تنظیمات Gunicorn (فایل را با -c به gunicorn بدهید). مقادیر با متغیر محیطی قابل تغییرند.

Worker count: by default it follows the CPUs *and* the memory the process may use (a cgroup/container limit counts), so a small VPS
is not overcommitted. Rule: ``min(2 × CPUs + 1, (memory − reserve) ÷ per-worker memory)``, at least 1 and at most 16, with
``reserve`` for the OS, PostgreSQL and nginx. Typical results: 1 GB → 1, **2 GB → 3 (1 CPU) or 5 (2+ CPUs)**, 4 GB → 5–9, 8 GB → 9–16.
Override with ``GUNICORN_WORKERS`` (or the standard ``WEB_CONCURRENCY``); tune the estimate with ``RAVIX_WORKER_MB`` and ``RAVIX_RESERVED_MB``.
"""
import os

WORKER_MB = 250      # a worker (Django + 2 threads + caches) after some hours of traffic
RESERVED_MB = 700    # operating system + PostgreSQL + nginx on the same server
MAX_WORKERS = 16


def _int_env(name: str, default: int | None = None) -> int | None:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _read(path: str) -> str:
    try:
        with open(path, encoding="ascii") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""


def cpu_count() -> int:
    """CPUs this process may use: affinity, then a container quota (cgroup v2 cpu.max / v1 cfs) if lower."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    quota = None
    parts = _read("/sys/fs/cgroup/cpu.max").split()
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) > 0:
        quota = int(parts[0]) / int(parts[1])
    else:
        q, p = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q.lstrip("-").isdigit() and p.isdigit() and int(q) > 0 and int(p) > 0:
            quota = int(q) / int(p)
    if quota:
        cpus = min(cpus, max(1, int(quota + 0.5)))
    return max(1, cpus)


def memory_mb() -> int | None:
    """Memory in MiB: the smaller of the machine's and the container's limit; None when neither can be read."""
    values = []
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            values.append(int(line.split()[1]) // 1024)
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read(path)
        if raw.isdigit() and int(raw) < 1 << 50:  # "max" or a huge number means "no limit"
            values.append(int(raw) // (1024 * 1024))
    return min(values) if values else None


def default_workers(cpus: int, mem_mb: int | None, worker_mb: int = WORKER_MB, reserved_mb: int = RESERVED_MB) -> int:
    by_cpu = cpus * 2 + 1
    if mem_mb is None:  # unknown memory: the CPU rule alone, but never above what a 2 GB server carries
        return max(1, min(by_cpu, 5, MAX_WORKERS))
    by_memory = (mem_mb - reserved_mb) // worker_mb
    return max(1, min(by_cpu, by_memory, MAX_WORKERS))


def choose_workers() -> int:
    explicit = _int_env("GUNICORN_WORKERS") or _int_env("WEB_CONCURRENCY")
    if explicit:
        return explicit
    return default_workers(cpu_count(), memory_mb(), _int_env("RAVIX_WORKER_MB", WORKER_MB), _int_env("RAVIX_RESERVED_MB", RESERVED_MB))


bind = os.environ.get("GUNICORN_BIND", "unix:/run/ravix/gunicorn.sock")
workers = choose_workers()
threads = _int_env("GUNICORN_THREADS", 2)
timeout = 30
graceful_timeout = 30
keepalive = _int_env("GUNICORN_KEEPALIVE", 65)  # longer than nginx's upstream keepalive_timeout (55 s): nginx closes idle connections first
max_requests = 1000          # بازیابی دوره‌ای ورکر (نشت حافظه)
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
loglevel = (os.environ.get("RAVIX_LOG_LEVEL") or os.environ.get("RAVIN_LOG_LEVEL") or "info").lower()
umask = 0o007                # سوکت فقط برای کاربر و گروه (nginx عضو گروه www-data است)
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")


def on_starting(server):
    server.log.info("Ravix: %s workers × %s threads (CPUs: %s, memory: %s MiB)", workers, threads, cpu_count(), memory_mb() or "unknown")
