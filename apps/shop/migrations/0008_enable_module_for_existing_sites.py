from django.db import migrations


def enable_for_existing(apps, schema_editor):
    """Ravix Shop became a switchable built-in module. A site that already has users (so: not a brand-new install) or any shop data keeps
    the shop on exactly as before; a brand-new install starts with it off until a superadmin activates it in Plugins."""
    ModuleState = apps.get_model("core", "ModuleState")
    if ModuleState.objects.filter(key="shop").exists():
        return
    User = apps.get_model(*__import__("django.conf", fromlist=["settings"]).settings.AUTH_USER_MODEL.split("."))
    Product, Order = apps.get_model("shop", "Product"), apps.get_model("shop", "Order")
    used = User.objects.exists() or Product.objects.exists() or Order.objects.exists()
    if used:  # no row = the module default (off in production, until the first activation)
        ModuleState.objects.create(key="shop", enabled=True, setup="done")


class Migration(migrations.Migration):
    dependencies = [("shop", "0007_shopsettings_store_info"), ("core", "0013_modulestate")]
    operations = [migrations.RunPython(enable_for_existing, migrations.RunPython.noop)]
