from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import get_script_prefix

from . import services, token

EXEMPT_PREFIXES = ("/install/", "/static/", "/theme-assets/", "/healthz")


class InstallGateMiddleware:
    """تا پایان نصب، همهٔ مسیرها (به‌جز /install/، استاتیک و /healthz) به /install/ می‌روند.

    پس از نصب، /install/ پاسخ ۴۰۴ می‌دهد (ویو). با ``RAVIX_INSTALLER_ENABLED=false`` کلاً خاموش است.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.RAVIX_INSTALLER_ENABLED and not request.path_info.startswith(EXEMPT_PREFIXES) and not services.is_installed():
            if token.required():
                token.expected_token()  # ساخت و چاپ توکن در لاگ در اولین بازدید
            return HttpResponseRedirect(get_script_prefix() + "install/")
        return self.get_response(request)
