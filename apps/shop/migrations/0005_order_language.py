from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shop", "0004_i18n_msgids")]
    operations = [
        migrations.AddField(
            model_name="order",
            name="language",
            field=models.CharField(blank=True, default="", editable=False, max_length=10, verbose_name="Language"),
        ),
    ]
