"""محاسبه آمار داشبورد از داده‌های واقعی پایگاه داده."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone

from apps.accounts.permissions import Role

User = get_user_model()


def site_stats() -> dict:
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    users = User.objects.all()
    by_role = {row["role"]: row["total"] for row in users.values("role").annotate(total=Count("id"))}
    return {
        "total_users": users.count(),
        "active_users": users.filter(is_active=True).count(),
        "inactive_users": users.filter(is_active=False).count(),
        "new_users_7d": users.filter(date_joined__gte=week_ago).count(),
        "new_users_30d": users.filter(date_joined__gte=month_ago).count(),
        "roles": [
            {"value": value, "label": label, "count": by_role.get(value, 0)} for value, label in Role.choices
        ],
    }


def content_stats(user) -> dict:
    """آمار محتوا؛ برای نویسنده فقط نوشته‌های خودش، برای مدیر کل سایت."""
    from apps.content.models import Page, Post, PublishStatus
    from apps.media_library.models import Media

    posts = Post.objects.all()
    if not user.has_cap("content.publish"):
        posts = posts.filter(author=user)
    data = {
        "posts_total": posts.count(),
        "posts_live": posts.live().count(),
        "posts_draft": posts.filter(status=PublishStatus.DRAFT).count(),
        "posts_scheduled": posts.scheduled_pending().count(),
        "scope_own": not user.has_cap("content.publish"),
    }
    if user.has_cap("pages.manage"):
        data["pages_total"] = Page.objects.count()
    if user.has_cap("media.upload"):
        data["media_total"] = Media.objects.count()
    return data


def latest_users(limit: int = 5):
    return User.objects.order_by("-date_joined")[:limit]


def profile_completeness(user) -> int:
    """درصد تکمیل پروفایل بر اساس فیلدهای واقعی کاربر."""
    checks = [user.first_name, user.last_name, user.email, user.phone, user.bio, user.avatar]
    filled = sum(1 for value in checks if value)
    return round(filled * 100 / len(checks))
