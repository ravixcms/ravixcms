#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def main():
    """Run administrative tasks."""
    # Shared-hosting packages carry a marker file next to manage.py; then the hosting settings are the default (a VPS never has it).
    from pathlib import Path

    here = Path(__file__).resolve().parent
    shared = (here / "ravix-hosting.marker").exists() or os.environ.get("RAVIX_HOSTING", "").strip().lower() in {"shared", "cpanel", "1", "true"}
    default = "config.settings.hosting" if shared else "config.settings.development"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", default)
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
