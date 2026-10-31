from django.utils.translation import gettext_lazy
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = gettext_lazy("User accounts")

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="accounts.users",
                label=gettext_lazy("Users"),
                url_name="accounts:user_list",
                icon="users",
                section="system",
                order=10,
                capability="users.view",
                active_urls=("accounts:user_list", "accounts:user_manage"),
            )
        )
        register_nav_item(
            NavItem(key="accounts.invitations", label=gettext_lazy("Invitations"), url_name="accounts:invitations", icon="users", section="system", order=11,
                    capability="users.manage", active_urls=("accounts:invitations",))
        )
        register_nav_item(
            NavItem(key="accounts.policy", label=gettext_lazy("Account settings"), url_name="accounts:policy", icon="settings", section="system", order=94,
                    capability="settings.manage", active_urls=("accounts:policy",))
        )
