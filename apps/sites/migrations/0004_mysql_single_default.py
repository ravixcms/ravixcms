"""MySQL/MariaDB: ``sites_single_default`` (a unique constraint with a condition) as a unique index on a generated column. Other databases: nothing to do."""
from django.db import migrations


def add(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        schema_editor.execute("ALTER TABLE `sites_site` ADD COLUMN `uq_default` TINYINT AS (IF(`is_default`, 1, NULL)) VIRTUAL, ADD UNIQUE INDEX `sites_single_default` (`uq_default`)")


def remove(apps, schema_editor):
    if schema_editor.connection.vendor == "mysql":
        schema_editor.execute("ALTER TABLE `sites_site` DROP INDEX `sites_single_default`, DROP COLUMN `uq_default`")


class Migration(migrations.Migration):
    dependencies = [("sites", "0003_i18n_msgids")]
    operations = [migrations.RunPython(add, remove, atomic=False)]
