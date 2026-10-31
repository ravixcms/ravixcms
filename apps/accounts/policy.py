"""Account rules in one place: who may sign up, and how."""
from apps.core.models import SiteSettings

from .models import AccountPolicy

OPEN, INVITE, CLOSED = "open", "invite", "closed"


def registration_mode(site=None, policy=None) -> str:
    """``open`` (anybody may sign up), ``invite`` (only people with an invitation) or ``closed`` (administrators create accounts)."""
    site = site or SiteSettings.load()
    policy = policy or AccountPolicy.load()
    if site.registration_open:
        return OPEN
    return INVITE if policy.invitations_enabled else CLOSED


def set_registration_mode(mode: str) -> None:
    """Writes both stored flags so that ``registration_mode()`` returns ``mode``."""
    site, policy = SiteSettings.load(), AccountPolicy.load()
    site.registration_open = mode == OPEN
    policy.invitations_enabled = mode in (OPEN, INVITE)
    site.save(update_fields=["registration_open"])
    policy.save(update_fields=["invitations_enabled"])
