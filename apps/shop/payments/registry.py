"""ثبت و دسترسی به درگاه‌ها: درگاه‌های کد + درگاه‌های افزونه‌های فعال/تأییدشده (hook ‎«shop.gateways»‎). فعال‌بودن هر درگاه برای هر سایت در GatewayConfig است."""
import logging
import os
import re

from django.conf import settings

from apps.core.deprecation import getenv

from . import vault
from .base import ConfigField, GatewayUnavailable, PaymentGateway

logger = logging.getLogger("ravix.shop.payments")

GATEWAYS: dict[str, type] = {}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,29}$")


def register_gateway(name: str, cls) -> None:
    """ثبت درگاه از کد. نام یکتا؛ کلاس باید زیرکلاس PaymentGateway باشد؛ بازنویسی ممنوع."""
    if not NAME_RE.match(name or "") or not (isinstance(cls, type) and issubclass(cls, PaymentGateway)):
        raise ValueError("نام یا کلاس درگاه نامعتبر است.")
    if name in GATEWAYS and GATEWAYS[name] is not cls:
        raise ValueError(f"درگاه «{name}» قبلاً ثبت شده است.")
    cls.name = name
    GATEWAYS[name] = cls


def available_gateways() -> dict:
    """همهٔ درگاه‌های موجود (فعال‌بودن جداست). افزونه‌ها نمی‌توانند درگاه موجود را بازنویسی کنند."""
    result = dict(GATEWAYS)
    try:
        from apps.plugins import hooks

        extra = hooks.apply_filter("shop.gateways", dict(GATEWAYS))
    except Exception:  # noqa: BLE001 - خرابی سیستم افزونه نباید پرداخت را بشکند
        return result
    if isinstance(extra, dict):
        for name, cls in extra.items():
            if name not in result and NAME_RE.match(str(name)) and isinstance(cls, type) and issubclass(cls, PaymentGateway):
                result[name] = cls
    return result


def sandbox_allowed() -> bool:
    return bool(settings.DEBUG or getattr(settings, "SHOP_ALLOW_SANDBOX_IN_PRODUCTION", False))


def env_override(name: str, key: str) -> str | None:
    """RAVIX_GATEWAY_<درگاه>_<کلید> در محیط بر دیتابیس مقدم است؛ برای نگهداری اسرار خارج از دیتابیس."""
    return getenv(f"RAVIX_GATEWAY_{name.upper()}_{key.upper()}") or None


def get_config_row(site, name):
    from ..models import GatewayConfig

    return GatewayConfig.objects.filter(site=site, gateway=name).first()


def load_config(cls, row) -> dict:
    """تنظیمات کامل (با اسرارِ رمزگشایی‌شده) برای استفادهٔ داخلی درگاه. هرگز به پاسخ/قالب نمی‌رود."""
    cfg = {f.key: f.default for f in cls.config_fields}
    if row is not None:
        cfg.update(row.public_config or {})
        cfg.update(vault.decrypt_json(row.secrets_encrypted))
    for f in cls.config_fields:
        override = env_override(cls.name, f.key)
        if override is not None:
            cfg[f.key] = override
    return cfg


def missing_fields(cls, cfg) -> list[ConfigField]:
    return [f for f in cls.config_fields if f.required and f.type != "bool" and not str(cfg.get(f.key, "")).strip()]


def is_enabled(site, name) -> bool:
    """فعال‌بودن: ردیف GatewayConfig(enabled). تنها استثنا: درگاه آزمایشیِ نام‌برده در SHOP_PAYMENT_GATEWAY وقتی برای آن هیچ ردیفی نیست
    و محیط توسعه/دمو است (راحتی توسعه؛ در تولید هرگز بدون فعال‌سازی صریح در پنل)."""
    cls = available_gateways().get(name)
    if cls is None:
        return False
    row = get_config_row(site, name)
    if row is not None:
        return row.enabled
    return bool(cls.is_test and name == settings.SHOP_PAYMENT_GATEWAY and sandbox_allowed())


def enabled_gateway_names(site) -> list[str]:
    return [n for n in available_gateways() if is_enabled(site, n)]


def get_gateway(name: str | None = None, site=None, require_enabled: bool = True) -> PaymentGateway:
    """نمونهٔ آمادهٔ درگاه برای سایت. اگر نام نیامد، درگاه پیش‌فرض (SHOP_PAYMENT_GATEWAY یا تنها درگاه فعال)."""
    if site is None:
        from apps.sites.models import Site

        site = Site.objects.filter(is_default=True).first()
    if not name:
        enabled = enabled_gateway_names(site)
        name = settings.SHOP_PAYMENT_GATEWAY if settings.SHOP_PAYMENT_GATEWAY in enabled else (enabled[0] if enabled else settings.SHOP_PAYMENT_GATEWAY)
    cls = available_gateways().get(name)
    if cls is not None and cls.is_test and not sandbox_allowed():
        raise GatewayUnavailable("درگاه آزمایشی در محیط تولید مجاز نیست؛ ابتدا یک درگاه واقعی متصل کنید.")
    if cls is None or (require_enabled and not is_enabled(site, name)):
        raise GatewayUnavailable("درگاه پرداخت فعال نیست.")
    row = get_config_row(site, name)
    try:
        cfg = load_config(cls, row)
    except vault.SecretsError as exc:
        raise GatewayUnavailable(str(exc))
    if missing_fields(cls, cfg):
        raise GatewayUnavailable("تنظیمات درگاه کامل نیست.")
    inst = cls(cfg, test_mode=True if row is None else row.test_mode)
    inst.name = name  # نام ثبت‌شده مرجع است (نه ویژگی کلاس)
    return inst


def secrets_of(gateway: PaymentGateway) -> list[str]:
    return vault.secret_values(gateway.config, gateway.config_fields)
