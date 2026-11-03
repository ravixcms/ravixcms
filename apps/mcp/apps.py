from django.apps import AppConfig
from django.utils.translation import gettext_lazy


class McpConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mcp"
    label = "mcp"
    verbose_name = gettext_lazy("Ravix MCP")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(key="mcp.settings", label=gettext_lazy("AI assistants (MCP)"), url_name="mcp:settings", icon="settings", section="system", order=95,
                    capability="mcp.manage", active_urls=("mcp:settings", "mcp:admin_connections"))
        )
