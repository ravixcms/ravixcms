from django.db import migrations, models


class Migration(migrations.Migration):
    """دامنهٔ اعتماد هر کلید (plugin / core). ردیف‌های موجود هر دو دامنه را نگه می‌دارند (رفتار قبلی)؛ مدیرکل در پنل محدودشان می‌کند."""

    dependencies = [("plugins", "0003_i18n_msgids")]

    operations = [
        migrations.AddField(
            model_name="trustedkey",
            name="scopes",
            field=models.CharField(default="plugin,core", max_length=20, verbose_name="Trust scope"),
        ),
    ]
