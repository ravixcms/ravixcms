from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_settings(apps, schema_editor):
    """Every existing site keeps the currency it has always used (the deployment's RAVIX_SHOP_CURRENCY_CODE), so stored amounts keep their meaning."""
    Site = apps.get_model("sites", "Site")
    ShopSettings = apps.get_model("shop", "ShopSettings")
    code = str(getattr(settings, "SHOP_CURRENCY_CODE", "IRT")).upper()
    if code not in ("IRR", "IRT", "USD", "EUR"):
        code = "IRT"
    for site in Site.objects.all():
        ShopSettings.objects.get_or_create(site=site, defaults={"currency": code})


class Migration(migrations.Migration):
    dependencies = [("shop", "0005_order_language"), ("sites", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="ShopSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created at")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last edit")),
                ("currency", models.CharField(default="IRT", max_length=3, verbose_name="Currency")),
                ("show_in_toman", models.BooleanField(default=False, help_text="Only for a rial shop: prices are shown and typed in toman, while rials stay stored and are what the gateway receives.", verbose_name="Show prices in toman")),
                ("low_stock_threshold", models.PositiveIntegerField(default=3, verbose_name="Low-stock alert at")),
                ("notify_customers", models.BooleanField(default=True, verbose_name="Email customers about order status")),
                ("site", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="shop_settings", to="sites.site")),
            ],
            options={"verbose_name": "Ravix Shop"},
        ),
        migrations.RunPython(create_settings, migrations.RunPython.noop),
    ]
