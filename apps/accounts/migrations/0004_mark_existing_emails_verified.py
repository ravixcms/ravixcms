from django.db import migrations
from django.utils import timezone


def mark_existing(apps, schema_editor):
    """کاربران موجود پیش از این قابلیت ثبت‌نام کرده‌اند؛ قفل نشوند (اگر تأیید ایمیل بعداً الزامی شود). فقط ستون خالی پر می‌شود."""
    User = apps.get_model("accounts", "User")
    User.objects.filter(email_verified_at__isnull=True).update(email_verified_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_user_email_verified_at")]
    operations = [migrations.RunPython(mark_existing, migrations.RunPython.noop)]
