"""
رجیستری منوی مدیریت.

هر ماژول (اپ) در متد ``AppConfig.ready`` آیتم‌های منوی خودش را ثبت می‌کند و
نیازی به ویرایش قالب اصلی نیست. این همان نقطه توسعه‌پذیری برای ماژول‌های
آینده (محتوا، رسانه، دسته‌بندی، افزونه‌ها و ...) است.
"""
from django.utils.translation import gettext_lazy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str = ""  # نام URL؛ یا به‌جای آن ``url`` ثابت (برای منوی افزونه‌ها)
    icon: str = "circle"
    section: str = "main"
    order: int = 100
    capability: str | None = None  # اگر تعیین شود فقط کاربران دارای این دسترسی می‌بینند
    active_urls: tuple[str, ...] = field(default_factory=tuple)  # نام view ها (namespace:name) برای فعال‌سازی
    url: str = ""


SECTIONS = {
    "main": "",
    "content": gettext_lazy("Content"),
    "appearance": gettext_lazy("Site appearance"),
    "system": gettext_lazy("System administration"),
}

_registry: dict[str, NavItem] = {}


def register_nav_item(item: NavItem) -> None:
    _registry[item.key] = item  # ثبت مجدد با همان key، آیتم قبلی را جایگزین می‌کند


def get_nav_items() -> list[NavItem]:
    return sorted(_registry.values(), key=lambda i: (list(SECTIONS).index(i.section), i.order))
