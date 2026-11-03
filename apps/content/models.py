from django.utils.translation import gettext, gettext_lazy
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.markup import render_markdown
from apps.core.models import TimeStampedModel
from apps.core.utils import RESERVED_PAGE_SLUGS, RandomUploadPath, unique_slug
from apps.core.validators import validate_image_file


class Category(TimeStampedModel):
    name = models.CharField(gettext_lazy("Name"), max_length=100, unique=True)
    slug = models.SlugField(gettext_lazy("Slug"), max_length=120, unique=True, allow_unicode=True, blank=True,
                            help_text=gettext_lazy("If left empty, it is generated automatically from the name."))
    description = models.TextField(gettext_lazy("Description"), blank=True, max_length=500)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children", verbose_name=gettext_lazy("Parent category"))

    class Meta:
        verbose_name = gettext_lazy("Content category")
        verbose_name_plural = gettext_lazy("Content categories")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Category, self.name, instance=self, fallback="category", max_length=120)
        super().save(*args, **kwargs)

    def descendant_ids(self) -> set[int]:
        found, frontier = set(), [self.pk]
        while frontier:
            children = list(Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
            frontier = [c for c in children if c not in found]
            found.update(children)
        return found


class Tag(TimeStampedModel):
    name = models.CharField(gettext_lazy("Name"), max_length=60, unique=True)
    slug = models.SlugField(gettext_lazy("Slug"), max_length=80, unique=True, allow_unicode=True, blank=True,
                            help_text=gettext_lazy("If left empty, it is generated automatically from the name."))

    class Meta:
        verbose_name = gettext_lazy("Tag")
        verbose_name_plural = gettext_lazy("Tags")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Tag, self.name, instance=self, fallback="tag", max_length=80)
        super().save(*args, **kwargs)


def default_content_language() -> str:
    """The site's default language code (a valid ``settings.LANGUAGES`` code)."""
    from apps.core.regional import regional_for

    from apps.core.models import SiteSettings

    return regional_for(SiteSettings.load())[0]


def _normalize_language(value: str) -> str:
    from django.conf import settings

    value = (value or "").strip().lower().replace("_", "-")
    return value if value in dict(settings.LANGUAGES) else ""


class PublishStatus(models.TextChoices):
    DRAFT = "draft", gettext_lazy("Draft")
    PUBLISHED = "published", gettext_lazy("Published")
    SCHEDULED = "scheduled", gettext_lazy("Scheduled")


class PostQuerySet(models.QuerySet):
    def live(self):
        now = timezone.now()
        return self.filter(Q(status=PublishStatus.PUBLISHED) | Q(status=PublishStatus.SCHEDULED, publish_at__lte=now))

    def scheduled_pending(self):
        return self.filter(status=PublishStatus.SCHEDULED, publish_at__gt=timezone.now())

    def drafts(self):
        return self.filter(status=PublishStatus.DRAFT)


class Post(TimeStampedModel):
    title = models.CharField(gettext_lazy("Title"), max_length=200)
    slug = models.SlugField(gettext_lazy("Slug (URL name)"), max_length=220, unique=True, allow_unicode=True, blank=True,
                            help_text=gettext_lazy("The last part of the post URL. If left empty, it is generated automatically from the title."))
    excerpt = models.TextField(gettext_lazy("Summary"), blank=True, max_length=500)
    body = models.TextField(gettext_lazy("Body (Markdown)"))
    body_html = models.TextField(editable=False, blank=True)  # خروجی پاک‌سازی‌شده؛ هنگام ذخیره ساخته می‌شود
    featured_image = models.ImageField(gettext_lazy("Featured image"), upload_to=RandomUploadPath("posts"), blank=True, validators=[validate_image_file])
    language = models.CharField(gettext_lazy("Language"), max_length=10, blank=True, db_index=True,
                                help_text=gettext_lazy("The language this text is written in. Visitors see it under this language's address."))
    status = models.CharField(gettext_lazy("Status"), max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT, db_index=True)
    publish_at = models.DateTimeField(gettext_lazy("Publish time"), null=True, blank=True, db_index=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="posts", verbose_name=gettext_lazy("Author"))
    categories = models.ManyToManyField(Category, blank=True, related_name="posts", verbose_name=gettext_lazy("Content categories"))
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts", verbose_name=gettext_lazy("Tags"))
    seo_title = models.CharField(gettext_lazy("SEO title"), max_length=70, blank=True, help_text=gettext_lazy("Preferably up to 60 characters. If left empty, the post title is used."))
    seo_description = models.CharField(gettext_lazy("SEO description (meta description)"), max_length=160, blank=True,
                                       help_text=gettext_lazy("Preferably 120 to 160 characters. If left empty, the post excerpt is used."))
    comments_open = models.BooleanField(gettext_lazy("Comments are open"), default=True,
                                        help_text=gettext_lazy("Unchecking this closes new comments on this post (existing comments remain visible)."))
    noindex = models.BooleanField(gettext_lazy("Hide from search engines (noindex)"), default=False,
                                  help_text=gettext_lazy("The post will not be listed in Google results and the like, and will not appear in the sitemap."))
    is_sample = models.BooleanField(gettext_lazy("Sample content"), default=False, editable=False, db_index=True,
                                    help_text=gettext_lazy("A sample post created during installation; after its first edit it no longer counts as a sample."))

    objects = PostQuerySet.as_manager()

    class Meta:
        verbose_name = gettext_lazy("Post")
        verbose_name_plural = gettext_lazy("Posts")
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.title

    # ------------------------------------------------------------ ذخیره
    def save(self, *args, **kwargs):
        self.language = _normalize_language(self.language) or default_content_language()
        if not self.slug:
            self.slug = unique_slug(Post, self.title, instance=self, fallback="post", max_length=220)
        if self.status == PublishStatus.PUBLISHED and self.publish_at is None:
            self.publish_at = timezone.now()
        if self.status == PublishStatus.DRAFT:
            self.publish_at = None
        self.body_html = render_markdown(self.body)
        old_image = None
        if self.pk:
            old_image = Post.objects.filter(pk=self.pk).values_list("featured_image", flat=True).first()
        super().save(*args, **kwargs)
        if old_image and old_image != self.featured_image.name:
            self.featured_image.storage.delete(old_image)  # حذف تصویر قبلی جایگزین‌شده

    @property
    def effective_title_tag(self) -> str:
        return self.seo_title or self.title

    # ------------------------------------------------------------ وضعیت
    @property
    def is_live(self) -> bool:
        if self.status == PublishStatus.PUBLISHED:
            return True
        return self.status == PublishStatus.SCHEDULED and self.publish_at is not None and self.publish_at <= timezone.now()

    @property
    def effective_status(self) -> str:
        """draft | published | scheduled (زمان‌بندی‌شده‌ای که هنوز نرسیده)"""
        if self.is_live:
            return PublishStatus.PUBLISHED
        return self.status

    @property
    def effective_status_label(self) -> str:
        return PublishStatus(self.effective_status).label

    # ------------------------------------------------------------ دسترسی
    def user_can_edit(self, user) -> bool:
        """مدیران همه را؛ نویسنده فقط پیش‌نویس‌های خودش را ویرایش/حذف می‌کند."""
        if not user.is_authenticated:
            return False
        if user.has_cap("content.publish"):
            return True
        return user.has_cap("content.write") and self.author_id == user.pk and self.status == PublishStatus.DRAFT

    def user_can_view(self, user) -> bool:
        if user.has_cap("content.publish"):
            return True
        return user.has_cap("content.write") and self.author_id == user.pk


class Page(TimeStampedModel):
    title = models.CharField(gettext_lazy("Title"), max_length=200)
    slug = models.SlugField(gettext_lazy("Slug (URL name)"), max_length=220, unique=True, allow_unicode=True, blank=True,
                            help_text=gettext_lazy("For example about-us. If left empty, it is generated automatically."))
    body = models.TextField(gettext_lazy("Content (Markdown)"))
    body_html = models.TextField(editable=False, blank=True)
    language = models.CharField(gettext_lazy("Language"), max_length=10, blank=True, db_index=True,
                                help_text=gettext_lazy("The language this text is written in. Visitors see it under this language's address."))
    status = models.CharField(gettext_lazy("Status"), max_length=12, choices=[(PublishStatus.DRAFT, gettext_lazy("Draft")), (PublishStatus.PUBLISHED, gettext_lazy("Published"))],
                              default=PublishStatus.DRAFT, db_index=True)
    seo_title = models.CharField(gettext_lazy("SEO title"), max_length=70, blank=True, help_text=gettext_lazy("Preferably up to 60 characters. If left empty, the page title is used."))
    seo_description = models.CharField(gettext_lazy("SEO description (meta description)"), max_length=160, blank=True, help_text=gettext_lazy("Preferably 120 to 160 characters."))
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="pages", verbose_name=gettext_lazy("Author"))
    is_sample = models.BooleanField(gettext_lazy("Sample content"), default=False, editable=False, db_index=True,
                                    help_text=gettext_lazy("A sample page created during installation; after its first edit it no longer counts as a sample."))

    class Meta:
        verbose_name = gettext_lazy("Page")
        verbose_name_plural = gettext_lazy("Pages")
        ordering = ["title"]

    def __str__(self):
        return self.title

    @property
    def is_live(self) -> bool:
        return self.status == PublishStatus.PUBLISHED

    @property
    def effective_title_tag(self) -> str:
        return self.seo_title or self.title

    def save(self, *args, **kwargs):
        self.language = _normalize_language(self.language) or default_content_language()
        if not self.slug:
            self.slug = unique_slug(Page, self.title, instance=self, fallback="page", max_length=220, reserved=RESERVED_PAGE_SLUGS)
        self.body_html = render_markdown(self.body)
        super().save(*args, **kwargs)


# ============================================================ ریدایرکت
RESERVED_REDIRECT_PREFIXES = ("/panel/", "/accounts/", "/api/", "/django-admin/", "/static/", "/media/", "/ext/", "/healthz", "/registry/", "/mcp", "/oauth/", "/.well-known/")


def normalize_redirect_path(value: str) -> str:
    """مسیر قدیم را به شکل ذخیره‌سازی (رمزگشایی‌شده، با «/» ابتدایی، بدون query/fragment) درمی‌آورد."""
    from urllib.parse import unquote

    value = unquote((value or "").strip())
    return value


class Redirect(TimeStampedModel):
    class Status(models.IntegerChoices):
        PERMANENT = 301, gettext_lazy("301 — permanent")
        TEMPORARY = 302, gettext_lazy("302 — temporary")

    old_path = models.CharField(gettext_lazy("Old path"), max_length=500, unique=True,
                                help_text=gettext_lazy("An internal site path, e.g. /blog/old-slug/ (without domain and without ?query)."))
    new_path = models.CharField(gettext_lazy("New path"), max_length=500,
                                help_text=gettext_lazy("An internal path (e.g. /blog/new-slug/) or a full http(s):// URL"))
    status_code = models.PositiveSmallIntegerField(gettext_lazy("Status code"), choices=Status.choices, default=Status.PERMANENT)
    is_auto = models.BooleanField(gettext_lazy("Created automatically"), default=False, editable=False,
                                  help_text=gettext_lazy("Created when a post/page slug was changed."))
    note = models.CharField(gettext_lazy("Note"), max_length=200, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Redirect")
        verbose_name_plural = gettext_lazy("Redirects")
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"{self.old_path} ← {self.new_path}"

    def clean(self):
        from urllib.parse import urlsplit

        from django.core.exceptions import ValidationError

        old = normalize_redirect_path(self.old_path)
        if not old.startswith("/") or old.startswith("//"):
            raise ValidationError({"old_path": gettext("The old path must start with “/” (e.g. /about/).")})
        if any(c in old for c in "?#") or any(ord(c) < 32 for c in old):
            raise ValidationError({"old_path": gettext("The old path must not contain ?, # or control characters.")})
        if old == "/":
            raise ValidationError({"old_path": gettext("Redirecting the home page is not allowed.")})
        if old.startswith(RESERVED_REDIRECT_PREFIXES):
            raise ValidationError({"old_path": gettext("This path belongs to system sections and cannot be redirected.")})
        self.old_path = old

        new = (self.new_path or "").strip()
        if any(ord(c) < 32 for c in new) or "\\" in new:
            raise ValidationError({"new_path": gettext("The new path is invalid.")})
        if new.startswith("/") and not new.startswith("//"):
            self.new_path = new
        elif new.lower().startswith(("http://", "https://")) and urlsplit(new).netloc:
            self.new_path = new
        else:
            raise ValidationError({"new_path": gettext("The new path must start with “/” or be a full http(s) URL.")})
        target = self.new_path.split("?", 1)[0].split("#", 1)[0]
        if self.new_path.startswith("/") and normalize_redirect_path(target).rstrip("/") == old.rstrip("/"):
            raise ValidationError({"new_path": gettext("The new path must not be the same as the old path (loop).")})
        # حلقهٔ مستقیم: مقصد خودش به مسیر قدیم ریدایرکت شود
        if self.new_path.startswith("/"):
            back = Redirect.objects.filter(old_path__in=[normalize_redirect_path(target), normalize_redirect_path(target).rstrip("/") or "/",
                                                         normalize_redirect_path(target).rstrip("/") + "/"]).exclude(pk=self.pk).first()
            if back is not None and normalize_redirect_path(back.new_path.split("?", 1)[0]).rstrip("/") == old.rstrip("/"):
                raise ValidationError({"new_path": gettext("This redirect forms a loop with another redirect.")})
