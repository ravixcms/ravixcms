from apps.core.clilang import cli_gettext
from django.utils.translation import gettext
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
    if getattr(settings, "RAVIX_HOSTING", False):  # shared hosting (cPanel): its own flow, with the one-time install code
        from .hosting_views import install as hosting_install

        return hosting_install(request)
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
                logger.exception(cli_gettext("Installation failed"))
                form.add_error(None, gettext("The installation ran into an error; details are in the server log. Check the database and migrations."))
            else:
                login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])
                messages.success(request, gettext("Installation complete. Welcome to Ravix."))
                return redirect("dashboard:home")
    else:
        form = InstallForm(db_ready=db_ready)
    return render(request, "installer/install.html", {"form": form, "checks": checks})
