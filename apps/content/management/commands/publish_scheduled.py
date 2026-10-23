"""تبدیل نوشته‌های زمان‌بندی‌شده‌ای که موعدشان رسیده به «منتشرشده» و ثبت رویداد انتشار.

نوشته‌های زمان‌بندی‌شده حتی بدون اجرای این دستور هنگام نمایش «منتشرشده» حساب می‌شوند
(``Post.is_live``)؛ این دستور فقط وضعیت ذخیره‌شده را همگام می‌کند و رویداد را در گزارش ثبت می‌کند.
برای اجرای دوره‌ای می‌توان آن را در cron گذاشت:  * * * * * python manage.py publish_scheduled
"""
from apps.core.clilang import cli_gettext
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import log_event
from apps.content.models import Post, PublishStatus


class Command(BaseCommand):
    help = cli_gettext("Publishes scheduled posts whose publish time has come.")

    def handle(self, *args, **options):
        due = Post.objects.filter(status=PublishStatus.SCHEDULED, publish_at__lte=timezone.now())
        count = 0
        for post in due:
            with transaction.atomic():
                post.status = PublishStatus.PUBLISHED  # publish_at همان زمان زمان‌بندی‌شده می‌ماند
                post.save(update_fields=["status", "updated_at"])
                log_event(None, AuditLog.Action.PUBLISH, post, cli_gettext("The post “%(post)s” was published on schedule.") % {"post": post})
            count += 1
        self.stdout.write(self.style.SUCCESS(cli_gettext("%(count)s posts were published.") % {"count": count}))
