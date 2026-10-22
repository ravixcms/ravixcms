from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    verbose_name = "داشبورد"

    def ready(self):
        from apps.core.navigation import NavItem, register_nav_item

        register_nav_item(
            NavItem(
                key="dashboard.home",
                label="داشبورد",
                url_name="dashboard:home",
                icon="home",
                section="main",
                order=0,
                active_urls=("dashboard:home",),
            )
        )
        register_nav_item(
            NavItem(
                key="accounts.profile",
                label="پروفایل من",
                url_name="accounts:profile",
                icon="user",
                section="main",
                order=10,
                active_urls=("accounts:profile", "accounts:password_change"),
            )
        )
