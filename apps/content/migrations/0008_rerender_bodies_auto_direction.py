"""Re-render the stored HTML of posts and pages so every text block carries dir="auto" (a Persian paragraph in an English page and
the reverse).

Safe on a large site (tens of thousands of rows):

* **Keyset batches** (``pk > last``, 200 rows at a time, ``only(pk, body, body_html)``): memory stays flat whatever the table size;
  nothing is loaded twice and no server-side cursor is held open.
* **Not one giant transaction** (``atomic = False``): every batch is committed on its own, so locks are short and a long run does not
  bloat the WAL / undo log. ``reset_queries()`` after each batch keeps memory flat even with DEBUG on. The step is **idempotent** (a row is written only when its HTML changes), so an interrupted run is simply
  run again and continues where the data still differs.
* Only ``body_html`` is written (``bulk_update``): no signals, no ``updated_at``, no redirects, no search/sitemap side effects.
* **Reversible**: going back re-renders the same way without ``dir="auto"``.
"""
from django.db import migrations, reset_queries

BATCH = 200
MODELS = ("Post", "Page")


def _convert(apps, auto_dir):
    from apps.core.markup import render_markdown

    for name in MODELS:
        model = apps.get_model("content", name)
        last = 0
        while True:
            rows = list(model.objects.filter(pk__gt=last).exclude(body="").only("pk", "body", "body_html").order_by("pk")[:BATCH])
            if not rows:
                break
            last = rows[-1].pk
            changed = []
            for row in rows:
                html = render_markdown(row.body, auto_dir=auto_dir)
                if html != row.body_html:
                    row.body_html = html
                    changed.append(row)
            if changed:
                model.objects.bulk_update(changed, ["body_html"])
            del rows, changed
            reset_queries()  # with DEBUG on Django keeps every SQL text (bulk_update statements are large)


def forwards(apps, schema_editor):
    _convert(apps, auto_dir=True)


def backwards(apps, schema_editor):
    _convert(apps, auto_dir=False)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("content", "0007_fix_language_slugs")]
    operations = [migrations.RunPython(forwards, backwards)]
