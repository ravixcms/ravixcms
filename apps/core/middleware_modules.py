from django.http import Http404

from . import modules


class ModuleGateMiddleware:
    """A disabled built-in module answers 404 on every route it owns (public pages, panel pages, API), whatever the view does."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        mod = modules.module_for_namespaces(match.namespaces if match else ()) or modules.module_for_path(request.path_info)
        if mod is not None and not modules.is_enabled(mod.key):
            raise Http404("module disabled")
        return None
