"""Re-render the stored HTML of posts and pages so every text block carries dir="auto" (a Persian paragraph in an English page and
the reverse). Only ``body_html`` is rewritten (queryset.update): no signals, no timestamps, no redirects."""
from django.db import migrations

BATCH = 200


def rerender(apps, schema_editor):
    from apps.core.markup import render_markdown

    for name in ("Post", "Page"):
        model = apps.get_model("content", name)
        batch = []
        for pk, body, html in model.objects.exclude(body="").values_list("pk", "body", "body_html").iterator(chunk_size=BATCH):
            new = render_markdown(body)
            if new != html:
                batch.append((pk, new))
            if len(batch) >= BATCH:
                _flush(model, batch)
                batch = []
        _flush(model, batch)


def _flush(model, batch):
    for pk, html in batch:
        model.objects.filter(pk=pk).update(body_html=html)


class Migration(migrations.Migration):
    dependencies = [("content", "0007_fix_language_slugs")]
    operations = [migrations.RunPython(rerender, migrations.RunPython.noop)]
