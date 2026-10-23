from django.db import migrations


def fix_language_slugs(apps, schema_editor):
    """Pages/posts upgraded from a version without language prefixes: rename slugs that are now language codes."""
    from apps.content import slug_audit

    slug_audit.fix(apps.get_model("content", "Page"), apps.get_model("content", "Post"),
                   apps.get_model("content", "Redirect"), apps.get_model("audit", "AuditLog"))


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0006_i18n_msgids"),
        ("audit", "0002_i18n_msgids"),
    ]
    operations = [migrations.RunPython(fix_language_slugs, migrations.RunPython.noop)]
