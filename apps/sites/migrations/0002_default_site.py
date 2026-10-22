from django.db import migrations


def create_default(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    if not Site.objects.filter(is_default=True).exists():
        Site.objects.get_or_create(slug="main", defaults={"name": "سایت اصلی", "is_default": True})


class Migration(migrations.Migration):
    dependencies = [("sites", "0001_initial")]
    operations = [migrations.RunPython(create_default, migrations.RunPython.noop)]
