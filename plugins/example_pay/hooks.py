def register(gateways, api, **_):
    """hook فیلتر «shop.gateways»: کلاس درگاه را به دیکشنری اضافه می‌کند. هسته نام را اعتبارسنجی می‌کند و بازنویسی درگاه موجود را نمی‌پذیرد."""
    try:
        from .gateway import ExamplePay
    except ImportError:  # ماژول فروشگاه خاموش است
        return gateways
    return {**gateways, ExamplePay.name: ExamplePay}
