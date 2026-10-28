from django.utils.translation import gettext_lazy
from django.conf import settings
from django.db import models

from .utils import RandomUploadPath
from .validators import validate_image_file


class TimeStampedModel(models.Model):
    """مدل پایه انتزاعی برای تمام مدل‌های آینده (محتوا، رسانه، ...)."""

    created_at = models.DateTimeField(gettext_lazy("Created at"), auto_now_add=True)
    updated_at = models.DateTimeField(gettext_lazy("Last edit"), auto_now=True)

    class Meta:
        abstract = True


class SiteSettings(models.Model):
    """تنظیمات عمومی سایت؛ همیشه فقط یک رکورد (singleton) وجود دارد."""

    site_name = models.CharField(gettext_lazy("Site name"), max_length=120, default=gettext_lazy("Ravix"))
    tagline = models.CharField(gettext_lazy("Site tagline"), max_length=200, blank=True)
    description = models.TextField(gettext_lazy("Site description"), blank=True, max_length=1000)
    logo = models.ImageField(
        gettext_lazy("Logo"), upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file]
    )
    favicon = models.ImageField(
        gettext_lazy("Favicon"), upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file]
    )
    default_share_image = models.ImageField(
        gettext_lazy("Default sharing image"), upload_to=RandomUploadPath("site"), blank=True, validators=[validate_image_file],
        help_text=gettext_lazy("When a post or page has no featured image, this image is shown in social media previews (og:image). Preferably 1200×630."),
    )
    admin_email = models.EmailField(gettext_lazy("Admin / support email"), blank=True)
    contact_phone = models.CharField(gettext_lazy("Contact phone"), max_length=30, blank=True)
    address = models.CharField(gettext_lazy("URL"), max_length=300, blank=True)
    footer_text = models.CharField(gettext_lazy("Footer text"), max_length=300, blank=True)
    language = models.CharField(gettext_lazy("Language"), max_length=10, default="fa")
    timezone = models.CharField(gettext_lazy("Time zone"), max_length=64, default="Asia/Tehran")
    installed_at = models.DateTimeField(gettext_lazy("Installation completed at"), null=True, blank=True, editable=False)
    onboarding_dismissed = models.BooleanField(gettext_lazy("Getting-started guide dismissed"), default=False, editable=False)
    registration_open = models.BooleanField(
        gettext_lazy("Public registration is enabled"),
        default=settings.REGISTRATION_DEFAULT_OPEN,
        help_text=gettext_lazy("If disabled, new users are created only by administrators."),
    )
    comments_require_approval = models.BooleanField(
        gettext_lazy("Comments require administrator approval before being shown"), default=True,
        help_text=gettext_lazy("If off, new (non-spam) comments are published immediately."))
    comments_notify_admin = models.BooleanField(gettext_lazy("Notify the admin email of new comments"), default=True,
                                                help_text=gettext_lazy("Sent to “Admin / support email”; if empty, no email is sent."))
    comments_blocked_words = models.TextField(
        gettext_lazy("Blocked words in comments"), blank=True, max_length=5000,
        help_text=gettext_lazy("One word or phrase per line. A comment that contains one of them (in the name, email or text) is recorded as “spam”."))
    content_per_language = models.BooleanField(
        gettext_lazy("Show each post and page only in its own language"), default=True,
        help_text=gettext_lazy("On: /en/ lists only English posts, a post opened under another language prefix is redirected to its own language, and each language has its own feed and sitemap. "
                               "Off: every post is shown under every language prefix (the behaviour before this option existed)."))
    translations = models.JSONField(default=dict, blank=True, editable=False)  # {language: {site_name, tagline, description, footer_text}}
    PAGE_CACHE_CHOICES = (("auto", gettext_lazy("Automatic (on for shared hosting, off for a VPS)")), ("on", gettext_lazy("On")), ("off", gettext_lazy("Off")))
    page_cache = models.CharField(gettext_lazy("Page cache for visitors"), max_length=4, choices=PAGE_CACHE_CHOICES, default="auto",
                                  help_text=gettext_lazy("Visitors who are not logged in get ready-made pages without database queries. It is cleared automatically whenever content changes. "
                                                         "Logged-in users, carts, forms and pages with a security token are never served from it."))
    keep_alive = models.BooleanField(gettext_lazy("Keep the application awake from the scheduled job"), default=True,
                                     help_text=gettext_lazy("The scheduled job opens the home page each time it runs so the first visitor does not wait for the application to start (shared hosting)."))
    updated_at = models.DateTimeField(gettext_lazy("Last edit"), auto_now=True)

    #: Texts a site owner can set per language. The plain fields above are the text in the site's default language and the
    #: fallback for every language that has no value of its own.
    TRANSLATABLE = ("site_name", "tagline", "description", "footer_text")

    class Meta:
        verbose_name = gettext_lazy("Site settings")
        verbose_name_plural = gettext_lazy("Site settings")

    def __str__(self):
        return self.site_name

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        kwargs.pop("force_insert", None)  # objects.create() روی رکورد موجود، آن را به‌روز می‌کند
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # جلوگیری از حذف تنظیمات
        pass

    def localized(self, field: str, language: str | None = None) -> str:
        """``field`` in ``language`` (default: the active one); falls back to the default-language text when there is none."""
        from .regional import normalize_language

        base = getattr(self, field)
        if field not in self.TRANSLATABLE:
            return base
        if language is None:
            from django.utils.translation import get_language

            language = get_language() or ""
        language = normalize_language(language)
        if not language or language == normalize_language(self.language):
            return base
        value = (self.translations or {}).get(language, {}).get(field, "") if isinstance(self.translations, dict) else ""
        return value if isinstance(value, str) and value.strip() else base

    def localized_copy(self, language: str | None = None) -> "SiteSettings":
        """A read-only copy whose translatable fields hold the text for ``language`` (for templates; never save it)."""
        import copy

        clone = copy.copy(self)
        for field in self.TRANSLATABLE:
            setattr(clone, field, self.localized(field, language))
        return clone

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ThrottleCacheEntry(models.Model):
    """جدول کش جایگزین (DatabaseCache با نام ``ravin_cache``) برای محدودسازی ورود وقتی کش اصلی خراب است.
    فقط از طریق مهاجرت ساخته می‌شود (بدون نیاز به ``createcachetable``)؛ ستون‌ها دقیقاً همان‌هایی‌اند که Django انتظار دارد."""

    cache_key = models.CharField(max_length=255, primary_key=True)
    value = models.TextField()
    expires = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "ravin_cache"
        verbose_name = gettext_lazy("Fallback cache entry")
        verbose_name_plural = gettext_lazy("Fallback cache entries")


class ModuleState(models.Model):
    """Runtime on/off switch (and setup-guide progress) of a built-in module such as Ravix Shop. Turning a module off hides it; it never
    deletes the module's data. Absent row = the module's default (see ``apps/core/modules.py``)."""

    SETUP_PENDING, SETUP_DISMISSED, SETUP_DONE = "", "dismissed", "done"

    key = models.SlugField(max_length=50, unique=True)
    enabled = models.BooleanField(default=False)
    setup = models.CharField(max_length=10, blank=True, default="")
    changed_at = models.DateTimeField(auto_now=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    def __str__(self):
        return f"{self.key}={'on' if self.enabled else 'off'}"


class MailSettings(models.Model):
    """SMTP settings edited in the panel (a singleton). When ``enabled`` is off, the server's own EMAIL_* settings are used.
    The password is stored encrypted (``apps.core.secretbox``)."""

    SECURITY_CHOICES = (("starttls", gettext_lazy("STARTTLS (usually port 587)")), ("ssl", gettext_lazy("SSL/TLS (usually port 465)")), ("none", gettext_lazy("No encryption (not recommended)")))

    enabled = models.BooleanField(gettext_lazy("Use these SMTP settings"), default=False)
    host = models.CharField(gettext_lazy("SMTP server"), max_length=255, blank=True)
    port = models.PositiveIntegerField(gettext_lazy("Port"), default=587)
    security = models.CharField(gettext_lazy("Encryption"), max_length=8, choices=SECURITY_CHOICES, default="starttls")
    username = models.CharField(gettext_lazy("Username"), max_length=255, blank=True)
    password_encrypted = models.TextField(blank=True, editable=False)
    from_email = models.CharField(gettext_lazy("Sender address"), max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = gettext_lazy("Email settings")
        verbose_name_plural = gettext_lazy("Email settings")

    def save(self, *args, **kwargs):
        self.pk = 1
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "MailSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class QueuedEmail(models.Model):
    """A message that could not be sent right away (the mail server was busy or down); ``ravix_cron`` retries it."""

    created_at = models.DateTimeField(auto_now_add=True)
    raw = models.BinaryField()  # the finished MIME message
    from_email = models.CharField(max_length=255)
    recipients = models.JSONField(default=list)
    attempts = models.PositiveSmallIntegerField(default=1)
    next_attempt = models.DateTimeField(db_index=True)
    last_error = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = gettext_lazy("Queued email")
        verbose_name_plural = gettext_lazy("Queued emails")


class CronRun(models.Model):
    """One run of ``manage.py ravix_cron``: when it ran and what each job did (shown in the site-health page)."""

    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=True)
    summary = models.JSONField(default=dict, blank=True)  # {job: "ok" | "skipped: …" | "failed: …"}

    class Meta:
        ordering = ["-started_at"]
        verbose_name = gettext_lazy("Scheduled-job run")
        verbose_name_plural = gettext_lazy("Scheduled-job runs")
