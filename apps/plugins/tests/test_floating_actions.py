"""The ``panel.floating_actions`` hook: an active plugin adds floating buttons to the panel; the core cleans what it returns."""
from django.test import Client

from .helpers import PluginTestCase, base_manifest

VIEWS = '''from django.http import HttpResponse


def home(request, api):
    return HttpResponse("home")


def ping(request, api):
    return HttpResponse("PONG")


def good(actions, api, request):
    return [
        {"label": "  Side   helper ", "icon": "edit", "url": "/panel/plugins/demo/home/"},
        {"label": "Admins only", "icon": "edit", "url": "/panel/plugins/demo/home/", "capability": "users.manage"},
        {"label": "Elsewhere", "icon": "edit", "url": "/accounts/login/"},                 # not under the panel: dropped
        {"label": "Evil", "icon": "edit", "url": "https://evil.example/panel/x/"},          # absolute: dropped
        {"label": "Evil2", "icon": "edit", "url": "//evil.example/panel/"},                 # protocol-relative: dropped
        {"label": "Markup", "icon": "<svg onload=x>", "url": "/panel/plugins/demo/home/"},  # icon is a name only: dropped
        {"label": "", "icon": "edit", "url": "/panel/plugins/demo/home/"},                  # empty label: dropped
        {"label": "Spaces", "icon": "edit", "url": "/panel/a b/"},                          # whitespace in the address: dropped
        "not a dict",
        {"label": "x" * 100, "icon": "edit", "url": "/panel/plugins/demo/home/?k=1"},       # the label is cut; the 3rd button of this plugin
        {"label": "Fourth", "icon": "edit", "url": "/panel/plugins/demo/home/"},            # over the per-plugin limit: dropped
    ]


def broken(actions, api, request):
    raise RuntimeError("boom")
'''


class FloatingActionsTests(PluginTestCase):
    def install_demo(self, handler="views:good", cap_of_user=None):
        manifest = base_manifest("demo", hooks=[{"name": "panel.floating_actions", "handler": handler}])
        self.write_plugin("demo", manifest, views=VIEWS)
        self.install_activate("demo")

    def page(self, user, url="/panel/"):
        c = Client()
        c.force_login(user)
        return c.get(url)

    def test_buttons_are_cleaned_and_rendered_for_an_admin(self):
        self.install_demo()
        html = self.page(self.superadmin).content.decode()
        self.assertIn('data-fab-open="/panel/plugins/demo/home/"', html)
        self.assertIn('data-fab-title="Side helper"', html)          # whitespace collapsed
        self.assertIn('data-fab-title="Admins only"', html)
        self.assertNotIn("evil.example", html)
        self.assertNotIn("Elsewhere", html)
        self.assertNotIn("onload=x", html)
        self.assertNotIn('data-fab-title="Spaces"', html)
        self.assertNotIn('data-fab-title="Fourth"', html)
        self.assertIn('data-fab-title="' + "x" * 40 + '"', html)      # label limited to 40 characters
        self.assertNotIn("x" * 41, html)
        self.assertEqual(html.count("data-fab-open="), 3)            # at most 3 per plugin

    def test_a_button_needing_a_capability_is_hidden_from_users_without_it(self):
        self.install_demo()
        html = self.page(self.author).content.decode()
        self.assertNotIn("Admins only", html)

    def test_no_button_when_no_plugin_uses_the_hook(self):
        self.write_plugin("demo", base_manifest("demo"))
        self.install_activate("demo")
        self.assertNotIn("data-fab-open", self.page(self.superadmin).content.decode())

    def test_a_broken_handler_never_breaks_the_panel(self):
        self.install_demo("views:broken")
        response = self.page(self.superadmin)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("data-fab-open", response.content.decode())

    def test_the_embedded_page_has_no_chrome_and_no_nested_buttons(self):
        self.install_demo()
        html = self.page(self.superadmin, "/panel/?embed=1").content.decode()
        self.assertIn("is-embedded", html)
        self.assertNotIn("data-fab-open", html)

    def test_buttons_are_not_added_to_public_pages_or_to_visitors(self):
        self.install_demo()
        self.assertNotIn("data-fab-open", Client().get("/fa/").content.decode())

    def test_only_the_panel_gets_buttons(self):
        self.install_demo()
        self.assertNotIn("data-fab-open", self.page(self.superadmin, "/accounts/profile/").content.decode())

    def test_the_total_is_limited(self):
        from apps.plugins.hooks import MAX_FLOATING_ACTIONS, sanitize_floating_actions

        many = [{"label": f"b{i}", "icon": "edit", "url": "/panel/x/"} for i in range(10)]
        out = []
        for _ in range(4):
            out = sanitize_floating_actions(out, many, self.superadmin)
        self.assertEqual(len(out), MAX_FLOATING_ACTIONS)

    def test_the_hook_name_is_accepted_in_a_manifest(self):
        from apps.plugins.hooks_registry import HOOKS

        self.assertEqual(HOOKS["panel.floating_actions"], "filter")

    def test_the_address_must_include_the_script_prefix_of_a_sub_folder_install(self):
        from django.urls import clear_script_prefix, set_script_prefix

        from apps.plugins.hooks import sanitize_floating_actions

        item = [{"label": "a", "icon": "edit", "url": "/panel/x/"}, {"label": "b", "icon": "edit", "url": "/site/panel/x/"}]
        set_script_prefix("/site/")
        try:
            out = sanitize_floating_actions([], item, self.superadmin)
        finally:
            clear_script_prefix()
        self.assertEqual([a["label"] for a in out], ["b"])
