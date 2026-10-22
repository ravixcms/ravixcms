from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.markup import render_markdown
from apps.core.models import TimeStampedModel
from apps.core.utils import RESERVED_PAGE_SLUGS, RandomUploadPath, unique_slug
from apps.core.validators import validate_image_file


class Category(TimeStampedModel):
    name = models.CharField("نام", max_length=100, unique=True)
    slug = models.SlugField("نامک", max_length=120, unique=True, allow_unicode=True, blank=True,
                            help_text="در صورت خالی بودن، خودکار از نام ساخته می‌شود.")
    description = models.TextField("توضیحات", blank=True, max_length=500)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children", verbose_name="دسته والد")

    class Meta:
        verbose_name = "دسته‌بندی"
        verbose_name_plural = "دسته‌بندی‌ها"
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
    name = models.CharField("نام", max_length=60, unique=True)
    slug = models.SlugField("نامک", max_length=80, unique=True, allow_unicode=True, blank=True,
                            help_text="در صورت خالی بودن، خودکار از نام ساخته می‌شود.")

    class Meta:
        verbose_name = "برچسب"
        verbose_name_plural = "برچسب‌ها"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Tag, self.name, instance=self, fallback="tag", max_length=80)
        super().save(*args, **kwargs)


class PublishStatus(models.TextChoices):
    DRAFT = "draft", "پیش‌نویس"
    PUBLISHED = "published", "منتشرشده"
    SCHEDULED = "scheduled", "زمان‌بندی‌شده"


class PostQuerySet(models.QuerySet):
    def live(self):
        now = timezone.now()
        return self.filter(Q(status=PublishStatus.PUBLISHED) | Q(status=PublishStatus.SCHEDULED, publish_at__lte=now))

    def scheduled_pending(self):
        return self.filter(status=PublishStatus.SCHEDULED, publish_at__gt=timezone.now())

    def drafts(self):
        return self.filter(status=PublishStatus.DRAFT)


class Post(TimeStampedModel):
    title = models.CharField("عنوان", max_length=200)
    slug = models.SlugField("نامک (Slug)", max_length=220, unique=True, allow_unicode=True, blank=True,
                            help_text="بخش انتهای نشانی نوشته. در صورت خالی بودن، خودکار از عنوان ساخته می‌شود.")
    excerpt = models.TextField("خلاصه", blank=True, max_length=500)
    body = models.TextField("متن (Markdown)")
    body_html = models.TextField(editable=False, blank=True)  # خروجی پاک‌سازی‌شده؛ هنگام ذخیره ساخته می‌شود
    featured_image = models.ImageField("تصویر شاخص", upload_to=RandomUploadPath("posts"), blank=True, validators=[validate_image_file])
    status = models.CharField("وضعیت", max_length=12, choices=PublishStatus.choices, default=PublishStatus.DRAFT, db_index=True)
    publish_at = models.DateTimeField("زمان انتشار", null=True, blank=True, db_index=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="posts", verbose_name="نویسنده")
    categories = models.ManyToManyField(Category, blank=True, related_name="posts", verbose_name="دسته‌بندی‌ها")
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts", verbose_name="برچسب‌ها")
    seo_title = models.CharField("عنوان سئو", max_length=70, blank=True, help_text="ترجیحاً تا ۶۰ کاراکتر. در صورت خالی بودن از عنوان نوشته استفاده می‌شود.")
    seo_description = models.CharField("توضیحات سئو (Meta Description)", max_length=160, blank=True,
                                       help_text="ترجیحاً ۱۲۰ تا ۱۶۰ کاراکتر. در صورت خالی بودن از خلاصه نوشته استفاده می‌شود.")
    comments_open = models.BooleanField("دیدگاه‌ها باز باشد", default=True,
                                        help_text="با برداشتن تیک، ارسال دیدگاه جدید برای این نوشته بسته می‌شود (دیدگاه‌های قبلی نمایش داده می‌شوند).")
    noindex = models.BooleanField("عدم نمایش در موتورهای جستجو (noindex)", default=False,
                                  help_text="نوشته در نتایج گوگل و ... فهرست نمی‌شود و در نقشه سایت (sitemap) نمی‌آید.")

    objects = PostQuerySet.as_manager()

    class Meta:
        verbose_name = "نوشته"
        verbose_name_plural = "نوشته‌ها"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.title

    # ------------------------------------------------------------ ذخیره
    def save(self, *args, **kwargs):
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
    title = models.CharField("عنوان", max_length=200)
    slug = models.SlugField("نامک (Slug)", max_length=220, unique=True, allow_unicode=True, blank=True,
                            help_text="مثلاً about-us یا درباره-ما. در صورت خالی بودن، خودکار ساخته می‌شود.")
    body = models.TextField("محتوا (Markdown)")
    body_html = models.TextField(editable=False, blank=True)
    status = models.CharField("وضعیت", max_length=12, choices=[(PublishStatus.DRAFT, "پیش‌نویس"), (PublishStatus.PUBLISHED, "منتشرشده")],
                              default=PublishStatus.DRAFT, db_index=True)
    seo_title = models.CharField("عنوان سئو", max_length=70, blank=True, help_text="ترجیحاً تا ۶۰ کاراکتر. در صورت خالی بودن از عنوان صفحه استفاده می‌شود.")
    seo_description = models.CharField("توضیحات سئو (Meta Description)", max_length=160, blank=True, help_text="ترجیحاً ۱۲۰ تا ۱۶۰ کاراکتر.")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="pages", verbose_name="نویسنده")

    class Meta:
        verbose_name = "صفحه"
        verbose_name_plural = "صفحات"
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
        if not self.slug:
            self.slug = unique_slug(Page, self.title, instance=self, fallback="page", max_length=220, reserved=RESERVED_PAGE_SLUGS)
        self.body_html = render_markdown(self.body)
        super().save(*args, **kwargs)


# ============================================================ ریدایرکت
RESERVED_REDIRECT_PREFIXES = ("/panel/", "/accounts/", "/api/", "/django-admin/", "/static/", "/media/", "/ext/", "/healthz", "/registry/")


def normalize_redirect_path(value: str) -> str:
    """مسیر قدیم را به شکل ذخیره‌سازی (رمزگشایی‌شده، با «/» ابتدایی، بدون query/fragment) درمی‌آورد."""
    from urllib.parse import unquote

    value = unquote((value or "").strip())
    return value


class Redirect(TimeStampedModel):
    class Status(models.IntegerChoices):
        PERMANENT = 301, "۳۰۱ — دائمی"
        TEMPORARY = 302, "۳۰۲ — موقت"

    old_path = models.CharField("مسیر قدیم", max_length=500, unique=True,
                                help_text="مسیر داخلی سایت، مثلاً /blog/نامک-قدیم/ (بدون دامنه و بدون ?query).")
    new_path = models.CharField("مسیر جدید", max_length=500,
                                help_text="مسیر داخلی (مثلاً /blog/نامک-جدید/) یا نشانی کامل http(s)://…")
    status_code = models.PositiveSmallIntegerField("کد وضعیت", choices=Status.choices, default=Status.PERMANENT)
    is_auto = models.BooleanField("ساخته‌شده خودکار", default=False, editable=False,
                                  help_text="با تغییر نامک نوشته/صفحه ساخته شده است.")
    note = models.CharField("یادداشت", max_length=200, blank=True)

    class Meta:
        verbose_name = "ریدایرکت"
        verbose_name_plural = "ریدایرکت‌ها"
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return f"{self.old_path} ← {self.new_path}"

    def clean(self):
        from urllib.parse import urlsplit

        from django.core.exceptions import ValidationError

        old = normalize_redirect_path(self.old_path)
        if not old.startswith("/") or old.startswith("//"):
            raise ValidationError({"old_path": "مسیر قدیم باید با «/» شروع شود (مثلاً /about/)."})
        if any(c in old for c in "?#") or any(ord(c) < 32 for c in old):
            raise ValidationError({"old_path": "مسیر قدیم نباید شامل ? یا # یا نویسهٔ کنترلی باشد."})
        if old == "/":
            raise ValidationError({"old_path": "ریدایرکت صفحهٔ اصلی مجاز نیست."})
        if old.startswith(RESERVED_REDIRECT_PREFIXES):
            raise ValidationError({"old_path": "این مسیر متعلق به بخش‌های سیستمی است و ریدایرکت‌پذیر نیست."})
        self.old_path = old

        new = (self.new_path or "").strip()
        if any(ord(c) < 32 for c in new) or "\\" in new:
            raise ValidationError({"new_path": "مسیر جدید نامعتبر است."})
        if new.startswith("/") and not new.startswith("//"):
            self.new_path = new
        elif new.lower().startswith(("http://", "https://")) and urlsplit(new).netloc:
            self.new_path = new
        else:
            raise ValidationError({"new_path": "مسیر جدید باید با «/» شروع شود یا نشانی کامل http(s) باشد."})
        target = self.new_path.split("?", 1)[0].split("#", 1)[0]
        if self.new_path.startswith("/") and normalize_redirect_path(target).rstrip("/") == old.rstrip("/"):
            raise ValidationError({"new_path": "مسیر جدید نباید با مسیر قدیم یکی باشد (حلقه)."})
        # حلقهٔ مستقیم: مقصد خودش به مسیر قدیم ریدایرکت شود
        if self.new_path.startswith("/"):
            back = Redirect.objects.filter(old_path__in=[normalize_redirect_path(target), normalize_redirect_path(target).rstrip("/") or "/",
                                                         normalize_redirect_path(target).rstrip("/") + "/"]).exclude(pk=self.pk).first()
            if back is not None and normalize_redirect_path(back.new_path.split("?", 1)[0]).rstrip("/") == old.rstrip("/"):
                raise ValidationError({"new_path": "این ریدایرکت با ریدایرکت دیگری حلقه می‌سازد."})
