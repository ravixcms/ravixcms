from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class PluginsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.plugins"
    verbose_name = gettext_lazy("Plugins")

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from apps.core.navigation import NavItem, register_nav_item

        from . import state
        from .models import PluginRecord

        post_save.connect(state.invalidate_signal, sender=PluginRecord, dispatch_uid="plugins.state.save")
        post_delete.connect(state.invalidate_signal, sender=PluginRecord, dispatch_uid="plugins.state.delete")
        register_nav_item(NavItem(key="plugins.list", label=gettext_lazy("Plugins"), url_name="plugins:list", icon="puzzle", section="system", order=60,
                                  capability="plugins.manage", active_urls=("plugins:list", "plugins:detail", "plugins:install", "plugins:settings")))
        register_nav_item(NavItem(key="plugins.events", label=gettext_lazy("Plugin log"), url_name="plugins:events", icon="list", section="system",
                                  order=61, capability="plugins.manage", active_urls=("plugins:events",)))
