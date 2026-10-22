from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "حساب‌های کاربری"

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="accounts.users",
                label="کاربران",
                url_name="accounts:user_list",
                icon="users",
                section="system",
                order=10,
                capability="users.view",
                active_urls=("accounts:user_list", "accounts:user_manage"),
            )
        )
