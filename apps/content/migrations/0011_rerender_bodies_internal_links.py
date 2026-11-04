"""Re-render the stored HTML of posts and pages so links to the site itself no longer carry ``nofollow``.

Same safe pattern as 0008: keyset batches, one commit per batch, idempotent (a row is written only when its HTML changes), only ``body_html``
is written (no signals, no ``updated_at``). Going back does nothing: the stored HTML stays valid either way.
"""
from django.db import migrations, reset_queries

BATCH = 200


def forwards(apps, schema_editor):
    from apps.core.markup import render_markdown

    for name in ("Post", "Page"):
        model = apps.get_model("content", name)
        last = 0
        while True:
            rows = list(model.objects.filter(pk__gt=last).exclude(body="").only("pk", "body", "body_html").order_by("pk")[:BATCH])
            if not rows:
                break
            last = rows[-1].pk
            changed = []
            for row in rows:
                html = render_markdown(row.body)
                if html != row.body_html:
                    row.body_html = html
                    changed.append(row)
            if changed:
                model.objects.bulk_update(changed, ["body_html"])
            del rows, changed
            reset_queries()


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("content", "0010_translation_group")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
