import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import services, token
from .forms import InstallForm

logger = logging.getLogger("ravix.installer")


def _checks():
    db_err = services.database_error()
    media = settings.MEDIA_ROOT
    import os

    writable = os.path.isdir(media) and os.access(media, os.W_OK)
    return {
        "db_error": db_err,
        "pending": services.pending_migrations() if not db_err else [],
        "media_ok": writable,
        "media_dir": str(media),
        "token_required": token.required(),
    }


@never_cache
@require_http_methods(["GET", "POST"])
def install(request):
    if not settings.RAVIX_INSTALLER_ENABLED or services.is_installed():
        raise Http404
    checks = _checks()
    db_ready = not checks["db_error"] and not checks["pending"]
    if request.method == "POST":
        form = InstallForm(request.POST, db_ready=db_ready)
        if form.is_valid() and not checks["db_error"]:
            d = form.cleaned_data
            try:
                if checks["pending"]:
                    services.run_migrations()
                user = services.perform_install(
                    site_name=d["site_name"], language=d["language"], timezone_name=d["timezone"],
                    username=d["username"], email=d["email"], password=d["password1"])
            except services.AlreadyInstalled:
                raise Http404 from None
            except services.InstallError as exc:
                form.add_error(None, str(exc))
            except Exception:  # noqa: BLE001
                logger.exception("نصب ناموفق بود")
                form.add_error(None, "نصب با خطا روبه‌رو شد؛ جزئیات در لاگ سرور است. دیتابیس و مهاجرت‌ها را بررسی کنید.")
            else:
                login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])
                messages.success(request, "نصب کامل شد. به Ravix خوش آمدید.")
                return redirect("dashboard:home")
    else:
        form = InstallForm(db_ready=db_ready)
    return render(request, "installer/install.html", {"form": form, "checks": checks})
