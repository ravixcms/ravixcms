"""Sites created before multilingual support were Persian, and Persian was also the hard-wired fallback language. The fallback is now
``settings.LANGUAGE_CODE`` (English by default), so an installed site whose stored default language is empty or no longer valid is
pinned to Persian here: an upgrade must never switch an existing Persian site to English. (Normal rows already hold "fa": the column
has been created with that default since the regional settings were added.)"""
from django.conf import settings
from django.db import migrations

LEGACY_LANGUAGE = "fa"


def pin_language(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    valid = {code for code, _name in settings.LANGUAGES}
    for row in SiteSettings.objects.filter(installed_at__isnull=False):
        if (row.language or "").strip().lower().replace("_", "-") not in valid:
            SiteSettings.objects.filter(pk=row.pk).update(language=LEGACY_LANGUAGE)


class Migration(migrations.Migration):
    dependencies = [("core", "0011_sitesettings_translations")]
    operations = [migrations.RunPython(pin_language, migrations.RunPython.noop)]
