from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class PluginHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pluginhub"
    label = "pluginhub"
    verbose_name = gettext_lazy("Plugin registry")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(NavItem(key="hub.directory", label="Plugin Directory", url_name="pluginhub:directory", icon="puzzle", section="system", order=62,
                                  capability="plugins.manage", active_urls=("pluginhub:directory", "pluginhub:directory_detail")))
        register_nav_item(NavItem(key="hub.publisher", label=gettext_lazy("Publish plugin"), url_name="pluginhub:my_plugins", icon="layers", section="system", order=63,
                                  active_urls=("pluginhub:my_plugins", "pluginhub:my_plugin", "pluginhub:upload", "pluginhub:tokens")))
        register_nav_item(NavItem(key="hub.admin", label=gettext_lazy("Registry management"), url_name="pluginhub:admin_publishers", icon="shield", section="system", order=64,
                                  capability="registry.manage", active_urls=("pluginhub:admin_publishers", "pluginhub:admin_categories", "pluginhub:admin_plugins")))
