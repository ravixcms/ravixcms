"""System emails: plain text stays the primary part; an HTML alternative adds the official Ravix logo.

The logo travels inside the message (a ``cid:`` inline PNG), so it shows without the recipient's client fetching anything from
the network and without the site needing a public URL. If the logo file or anything in the HTML step fails, the plain-text
message is sent exactly as before: a mail is never lost because of its decoration.
"""
import logging
import re
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

from apps.core.clilang import cli_gettext

logger = logging.getLogger(__name__)

LOGO_PATH = Path(settings.BASE_DIR) / "static" / "brand" / "png" / "ravix-logo.png"
LOGO_CID = "ravix-logo"
LOGO_WIDTH = 132  # CSS pixels (the PNG is 1464x480, i.e. crisp at 3x)
_URL = re.compile(r"(https?://[^\s<>\"']+)")


def _html_body(text: str) -> str:
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        safe = escape(block).replace("\n", "<br>")
        paragraphs.append('<p style="margin:0 0 16px">' + _URL.sub(r'<a href="\1" style="color:#4F46E5">\1</a>', safe) + "</p>")
    return "".join(paragraphs)


def render_html(text: str, *, direction: str = "ltr", logo: bool = True) -> str:
    align = "right" if direction == "rtl" else "left"
    head = (f'<div style="padding:0 0 20px;text-align:{align}"><img src="cid:{LOGO_CID}" alt="Ravix" width="{LOGO_WIDTH}" '
            f'style="display:inline-block;border:0;height:auto;width:{LOGO_WIDTH}px"></div>') if logo else ""
    return ('<!doctype html><html dir="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            '<body style="margin:0;padding:24px 12px;background:#f4f6fb"><div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;'
            'padding:28px;font-family:Tahoma,Vazirmatn,-apple-system,Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.8;color:#0F172A;'
            'direction:%s;text-align:%s">%s%s</div></body></html>') % (direction, direction, align, head, _html_body(text))


def build_message(subject: str, text: str, to: list[str], from_email: str | None = None) -> EmailMultiAlternatives:
    """A text message with the branded HTML alternative attached when possible."""
    from django.utils.translation import get_language_bidi

    message = EmailMultiAlternatives(subject, text, from_email, to)
    try:
        png = LOGO_PATH.read_bytes()
        message.attach_alternative(render_html(text, direction="rtl" if get_language_bidi() else "ltr"), "text/html")
        message.mixed_subtype = "related"
        image = MIMEImage(png, _subtype="png")
        image.add_header("Content-ID", f"<{LOGO_CID}>")
        image.add_header("Content-Disposition", "inline", filename="ravix-logo.png")
        message.attach(image)
    except Exception:  # noqa: BLE001 - decoration only; the plain-text message is still complete
        logger.warning(cli_gettext("The branded HTML part of the email could not be built; sending plain text only."), exc_info=True)
        message = EmailMultiAlternatives(subject, text, from_email, to)
    return message
