"""محاسبات رنگ: کنتراست WCAG و ساخت رنگ‌های مشتق (سطح، حاشیه، متن کم‌رنگ) با تضمین خوانایی."""
from .constants import MIN_CONTRAST


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def _channel(c: int) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    r, g, b = hex_to_rgb(color)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, ratio_b: float) -> str:
    """ratio_b سهم رنگ b در ترکیب است (۰ تا ۱)."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x * (1 - ratio_b) + y * ratio_b for x, y in zip(ra, rb)))


def on_color(background: str) -> str:
    """رنگ متنی (سفید یا تقریباً سیاه) که روی background بیشترین کنتراست را دارد."""
    return "#ffffff" if contrast("#ffffff", background) >= contrast("#111111", background) else "#111111"


def derived_palette(primary: str, background: str, text: str) -> dict[str, str]:
    surface = mix(background, text, 0.045)
    border = mix(background, text, 0.16)
    muted = text
    for ratio in (0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10, 0.05):
        candidate = mix(text, background, ratio)
        if contrast(candidate, background) >= MIN_CONTRAST + 0.1 and contrast(candidate, surface) >= MIN_CONTRAST:
            muted = candidate
            break
    return {
        "primary": primary,
        "on_primary": on_color(primary),
        "primary_soft": mix(background, primary, 0.10),
        "background": background,
        "surface": surface,
        "border": border,
        "text": text,
        "muted": muted,
    }


def palette_errors(primary: str, background: str, text: str) -> list[str]:
    """خطاهای دسترس‌پذیری ترکیب رنگ‌ها (فهرست خالی یعنی معتبر)."""
    errors = []
    surface = mix(background, text, 0.045)
    if contrast(text, background) < MIN_CONTRAST:
        errors.append(
            f"کنتراست «رنگ متن» و «رنگ پس‌زمینه» کافی نیست ({contrast(text, background):.1f} از حداقل {MIN_CONTRAST}). "
            "رنگ متن را تیره‌تر یا پس‌زمینه را روشن‌تر کنید."
        )
    worst = min(contrast(primary, background), contrast(primary, surface))
    if worst < MIN_CONTRAST:
        errors.append(
            f"کنتراست «رنگ اصلی» با پس‌زمینه کافی نیست ({worst:.1f} از حداقل {MIN_CONTRAST}). پیوندها با این رنگ خوانا نمی‌شوند؛ رنگ اصلی را پررنگ‌تر کنید."
        )
    return errors
