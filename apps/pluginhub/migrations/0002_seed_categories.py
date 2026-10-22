from django.db import migrations

CATS = [("forms", "فرم‌ها", 10), ("seo", "سئو و بازاریابی", 20), ("commerce", "فروشگاه و پرداخت", 30), ("content", "محتوا", 40),
        ("communication", "ارتباط و پشتیبانی", 50), ("security", "امنیت", 60), ("tools", "ابزارها", 90)]


def seed(apps, schema_editor):
    Category = apps.get_model("pluginhub", "Category")
    for slug, name, order in CATS:
        Category.objects.get_or_create(slug=slug, defaults={"name": name, "order": order})


class Migration(migrations.Migration):
    dependencies = [("pluginhub", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
