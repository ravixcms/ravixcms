"""content.0008 (re-render bodies with dir="auto") on a large table: batched, flat memory, idempotent, reversible."""
import importlib
import tracemalloc

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.content.models import Page, Post
from apps.core.markup import render_markdown

migration = importlib.import_module("apps.content.migrations.0008_rerender_bodies_auto_direction")
BODY = "## عنوان\n\nمتن فارسی با **تاکید** و [پیوند](https://example.com).\n\n- یک\n- دو\n\n" + "Lorem ipsum dolor sit amet. " * 40


def make_posts(n, start=0):
    Post.objects.bulk_create([Post(title=f"p{i}", slug=f"p{i}", body=BODY, body_html=render_markdown(BODY, auto_dir=False), status="draft")
                              for i in range(start, start + n)], batch_size=500)


class RerenderMigrationTests(TestCase):
    def html(self, pk):
        return Post.objects.values_list("body_html", flat=True).get(pk=pk)

    def test_forwards_adds_dir_and_backwards_removes_it(self):
        make_posts(5)
        Page.objects.bulk_create([Page(title="درباره", slug="about", body="متن", body_html="<p>متن</p>", status="published")])
        migration.forwards(apps, None)
        for html in Post.objects.values_list("body_html", flat=True):
            self.assertIn('<p dir="auto">', html)
            self.assertEqual(html, render_markdown(BODY))
        self.assertEqual(Page.objects.get().body_html, '<p dir="auto">متن</p>')
        migration.backwards(apps, None)
        for html in Post.objects.values_list("body_html", flat=True):
            self.assertNotIn("dir=", html)
            self.assertEqual(html, render_markdown(BODY, auto_dir=False))
        self.assertEqual(Page.objects.get().body_html, "<p>متن</p>")

    def test_only_body_html_changes_and_is_idempotent(self):
        make_posts(3)
        before = list(Post.objects.order_by("pk").values("title", "slug", "body", "status", "updated_at", "publish_at"))
        migration.forwards(apps, None)
        self.assertEqual(before, list(Post.objects.order_by("pk").values("title", "slug", "body", "status", "updated_at", "publish_at")))
        with CaptureQueriesContext(connection) as again:
            migration.forwards(apps, None)  # second run: reads, but writes nothing
        self.assertFalse([q for q in again.captured_queries if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT"))])

    def test_rows_without_a_body_are_skipped_and_empty_table_is_fine(self):
        migration.forwards(apps, None)
        Post.objects.bulk_create([Post(title="e", slug="e", body="", body_html="", status="draft")])
        migration.forwards(apps, None)
        self.assertEqual(Post.objects.get().body_html, "")

    def test_queries_grow_with_batches_not_rows_and_memory_is_flat(self):
        make_posts(300)
        with CaptureQueriesContext(connection) as small:
            tracemalloc.start()
            migration.forwards(apps, None)
            small_peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        migration.backwards(apps, None)
        make_posts(1200, start=300)  # 1500 rows: five times as many
        with CaptureQueriesContext(connection) as large:
            tracemalloc.start()
            migration.forwards(apps, None)
            large_peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        batches = -(-1500 // migration.BATCH)
        self.assertLessEqual(len(large.captured_queries), 3 * batches + 10)  # one read + one write per batch (+ the Page table)
        self.assertLess(len(large.captured_queries), len(small.captured_queries) * 6)
        # five times the rows must not mean five times the memory (a whole-table load would): allow generous headroom
        self.assertLess(large_peak, small_peak * 2.5, (small_peak, large_peak))
