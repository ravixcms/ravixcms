from django.db import migrations, models


def report_members(apps, schema_editor):
    """Plain "user" accounts used to open the panel; now they do not. Count them, list them in the migration report and in the admin events."""
    User = apps.get_model("accounts", "User")
    names = list(User.objects.filter(role="user", is_active=True).order_by("username").values_list("username", flat=True))
    if not names:
        return
    SiteSettings = apps.get_model("core", "SiteSettings")
    SiteSettings.objects.get_or_create(pk=1)
    SiteSettings.objects.filter(pk=1).update(members_notice_count=len(names))
    shown = ", ".join(names[:150])
    print(f"\n  [ravix] Panel access of the role 'user' was removed (it is now a member). Affected users ({len(names)}): {shown}{' …' if len(names) > 150 else ''}")
    AuditLog = apps.get_model("audit", "AuditLog")
    AuditLog.objects.create(action="update", object_type="Role", object_repr="user", message=(f"Panel access removed from the role 'user'. Affected: {shown}")[:400])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_hosting_mail_cron_pagecache"),
        ("accounts", "0006_user_language"),
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AddField(model_name="sitesettings", name="members_notice_count", field=models.PositiveIntegerField(default=0, editable=False)),
        migrations.AlterField(model_name="sitesettings", name="registration_open", field=models.BooleanField(default=False, help_text="If disabled, new users are created only by administrators.", verbose_name="Public registration is enabled")),
        migrations.RunPython(report_members, migrations.RunPython.noop),
    ]
