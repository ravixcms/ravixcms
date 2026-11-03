"""A person flagged "must choose a new password" can do nothing else until they have: every request except the password page, sign-out,
the language switch and static files is sent to the password page (JSON clients get 403)."""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext

OPEN_PREFIXES = ("/static/", "/media/", "/theme-assets/", "/studio-assets/", "/i18n/", "/jsi18n/", "/healthz")


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "must_change_password", False):
            path = request.path_info
            change = reverse("accounts:password_change")
            if not (path.startswith(OPEN_PREFIXES) or path in (change, reverse("accounts:logout"))):
                if path.startswith("/api/") or request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({"detail": gettext("You must choose a new password before you can continue.")}, status=403)
                if request.method in ("GET", "HEAD"):
                    messages.warning(request, gettext("You must choose a new password before you can continue."))
                nxt = request.get_full_path() if request.method in ("GET", "HEAD") else ""
                return redirect(change + (f"?{urlencode({'next': nxt})}" if nxt else ""))
        return self.get_response(request)
