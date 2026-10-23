from apps.core.clilang import cli_gettext
import socket

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = cli_gettext("Sends a test email using the current email settings to check SMTP. The SMTP password is never printed.")

    def add_arguments(self, parser):
        parser.add_argument("to", help=cli_gettext("Recipient email address"))
        parser.add_argument("--subject", default=cli_gettext("Ravix test email"))

    def handle(self, *args, to, subject, **options):
        if "@" not in to or " " in to:
            raise CommandError(cli_gettext("The email address is not valid."))
        backend = settings.EMAIL_BACKEND
        self.stdout.write(f"backend:  {backend}")
        if backend.endswith("smtp.EmailBackend"):
            user_state = cli_gettext("set") if settings.EMAIL_HOST_USER else cli_gettext("empty")
            password_state = cli_gettext("set") if settings.EMAIL_HOST_PASSWORD else cli_gettext("empty")
            self.stdout.write(cli_gettext("smtp:     %(a1)s:%(a2)s (TLS=%(a3)s, user=%(user_state)s, password=%(password_state)s)") % {"a1": settings.EMAIL_HOST, "a2": settings.EMAIL_PORT, "a3": settings.EMAIL_USE_TLS, "user_state": user_state, "password_state": password_state})
        self.stdout.write(f"from:     {settings.DEFAULT_FROM_EMAIL}")
        body = cli_gettext("This is a test message; it shows that Ravix email is being sent from %(a1)s.") % {"a1": socket.gethostname()}
        try:
            sent = EmailMessage(subject, body, to=[to]).send(fail_silently=False)
        except Exception as exc:  # noqa: BLE001 - هر خطای SMTP/شبکه را روشن گزارش بده
            raise CommandError(cli_gettext("Sending failed: %(a1)s: %(exc)s") % {"a1": type(exc).__name__, "exc": exc}) from None
        if not sent:
            raise CommandError(cli_gettext("Sending failed (the backend accepted no message)."))
        if backend.endswith("console.EmailBackend"):
            self.stdout.write(self.style.WARNING(cli_gettext("The backend is the console one: the message was printed above and no real email was sent (development mode).")))
        else:
            self.stdout.write(self.style.SUCCESS(cli_gettext("The email was handed to the backend (to %(to)s). Check yourself that it reaches the inbox.") % {"to": to}))
