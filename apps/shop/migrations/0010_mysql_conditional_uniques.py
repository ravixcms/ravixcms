"""MySQL/MariaDB cannot create unique constraints with a condition (PostgreSQL and SQLite can). The same guarantees are added there as unique
indexes on generated columns, so a duplicate still raises IntegrityError at the database level. Other databases: nothing to do."""
from django.db import migrations

TABLES = (
    # table, generated column, expression, index name
    ("shop_payment", "uq_success_order", "IF(`status` = 'succeeded', `order_id`, NULL)", "shop_payment_one_success_per_order"),
    ("shop_payment", "uq_gateway_ref", "IF(`gateway_ref` <> '', CONCAT(`gateway`, ':', `gateway_ref`), NULL)", "shop_payment_unique_gateway_ref"),
    ("shop_product", "uq_sku", "IF(`sku` <> '', CONCAT(`site_id`, ':', `sku`), NULL)", "shop_product_unique_sku_per_site"),
    ("shop_productvariant", "uq_sku", "IF(`sku` <> '', CONCAT(`site_id`, ':', `sku`), NULL)", "shop_variant_unique_sku_per_site"),
)
TYPES = {"uq_success_order": "BIGINT", "uq_gateway_ref": "VARCHAR(200)", "uq_sku": "VARCHAR(100)"}


def add(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    for table, column, expr, index in TABLES:
        schema_editor.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {TYPES[column]} AS ({expr}) VIRTUAL, ADD UNIQUE INDEX `{index}` (`{column}`)")


def remove(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    for table, column, _expr, index in reversed(TABLES):
        schema_editor.execute(f"ALTER TABLE `{table}` DROP INDEX `{index}`, DROP COLUMN `{column}`")


class Migration(migrations.Migration):
    dependencies = [("shop", "0009_catalog_variants_tags")]
    operations = [migrations.RunPython(add, remove, atomic=False)]
