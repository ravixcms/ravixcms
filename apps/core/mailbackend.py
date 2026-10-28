"""Email transport for sites without a dedicated mail service (shared hosting).

* ``DirectEmailBackend``: SMTP with the settings saved in the panel (Email settings) when they are turned on, otherwise the server's EMAIL_* settings.
* ``QueuedEmailBackend``: tries to send right away; if the mail server is busy or down the message is kept in the database and
  ``manage.py ravix_cron`` retries it (5 minutes, 15 minutes, 1 hour, 6 hours; then it is dropped). A visitor never waits for a retry.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.utils.translation import gettext
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from django.db import DatabaseError
from django.utils import timezone

from .clilang import cli_gettext

logger = logging.getLogger("ravix.mail")

RETRY_DELAYS = (timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1), timedelta(hours=6))
DIRECT = "apps.core.mailbackend.DirectEmailBackend"


def panel_settings():
    """The saved panel settings if they are on and complete, else None."""
    from . import secretbox
    from .models import MailSettings

    try:
        row = MailSettings.objects.filter(pk=1).first()
    except DatabaseError:
        return None
    if not row or not row.enabled or not row.host:
        return None
    return {"host": row.host, "port": row.port, "username": row.username, "password": secretbox.decrypt(row.password_encrypted),
            "use_tls": row.security == "starttls", "use_ssl": row.security == "ssl", "from_email": row.from_email}


class DirectEmailBackend(SmtpBackend):
    def __init__(self, host=None, port=None, username=None, password=None, use_tls=None, fail_silently=False, use_ssl=None, timeout=None, **kwargs):
        self.panel_from = ""
        panel = panel_settings() if host is None else None
        if panel:
            host, port, username, password = panel["host"], panel["port"], panel["username"], panel["password"]
            use_tls, use_ssl, self.panel_from = panel["use_tls"], panel["use_ssl"], panel["from_email"]
        super().__init__(host=host, port=port, username=username, password=password, use_tls=use_tls, fail_silently=fail_silently, use_ssl=use_ssl,
                         timeout=timeout, **kwargs)

    def send_messages(self, email_messages):
        if self.panel_from:
            for message in email_messages:
                if message.from_email == settings.DEFAULT_FROM_EMAIL:
                    message.from_email = self.panel_from
        return super().send_messages(email_messages)

    def send_raw(self, from_email, recipients, raw: bytes) -> None:
        """Send a stored message. Raises on failure (the caller decides about retries)."""
        opened = self.open()
        try:
            self.connection.sendmail(from_email, recipients, raw)
        finally:
            if opened:
                self.close()


class QueuedEmailBackend(DirectEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        for message in email_messages:
            try:
                sent += super().send_messages([message]) or 0
            except Exception as exc:  # noqa: BLE001 - any transport error: keep the message for a retry
                if self._enqueue(message, exc):
                    sent += 1
                elif not self.fail_silently:
                    raise
        return sent

    def _enqueue(self, message, exc) -> bool:
        from .models import QueuedEmail

        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            raw = message.message().as_bytes(linesep="\r\n")
            QueuedEmail.objects.create(raw=raw, from_email=message.from_email, recipients=list(message.recipients()), attempts=1,
                                       next_attempt=timezone.now() + RETRY_DELAYS[0], last_error=type(exc).__name__)
            logger.warning(cli_gettext("An email could not be sent now (%(error)s); it was queued for a retry."), {"error": type(exc).__name__})
            return True
        except Exception:  # noqa: BLE001
            logger.exception(cli_gettext("An email could not be sent and could not be queued either."))
            return False


def send_queued(limit: int = 40) -> dict:
    """Try the due messages once. Returns counts for the cron summary."""
    from .models import QueuedEmail

    now = timezone.now()
    done = {"sent": 0, "retry": 0, "dropped": 0}
    backend = DirectEmailBackend(fail_silently=False)
    for item in QueuedEmail.objects.filter(next_attempt__lte=now).order_by("next_attempt")[:limit]:
        try:
            backend.send_raw(item.from_email, item.recipients, bytes(item.raw))
        except Exception as exc:  # noqa: BLE001
            if item.attempts >= len(RETRY_DELAYS) + 1:
                item.delete()
                done["dropped"] += 1
                logger.error(cli_gettext("A queued email was dropped after %(n)s attempts (%(error)s)."), {"n": item.attempts, "error": type(exc).__name__})
            else:
                item.next_attempt = now + RETRY_DELAYS[min(item.attempts, len(RETRY_DELAYS) - 1)]
                item.attempts += 1
                item.last_error = type(exc).__name__
                item.save(update_fields=["next_attempt", "attempts", "last_error"])
                done["retry"] += 1
        else:
            item.delete()
            done["sent"] += 1
    return done


def uses_panel_mail() -> bool:
    """True when this site sends through Ravix's own mail backend (so the panel's Email settings page is what configures it)."""
    return str(getattr(settings, "EMAIL_BACKEND", "")).startswith("apps.core.mailbackend.")


def send_test(to: str) -> None:
    """Send the test message right now, bypassing the retry queue, so a wrong setting raises an error the owner can read."""
    from django.core.mail import get_connection

    from .mailhtml import build_message
    from .recipient import current_language, language_for_email, use_language

    with use_language(language_for_email(to, fallback=current_language())):  # in the language of the person who will read it
        message = build_message(gettext("Ravix test email"), gettext("This is a test message and shows that sending email from your site works."), [to])
    connection = DirectEmailBackend(fail_silently=False) if uses_panel_mail() else get_connection(fail_silently=False)
    message.connection = connection
    if not message.send(fail_silently=False):
        raise RuntimeError(gettext("the mail service did not accept the message"))
