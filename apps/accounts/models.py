from django.utils.translation import gettext_lazy
from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.utils import RandomUploadPath
from apps.core.validators import validate_image_file

from .permissions import ROLE_CAPABILITIES, ROLE_LEVEL, Role


class UserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """کاربر سفارشی Ravix با نقش، پروفایل و ایمیل یکتا."""

    email = models.EmailField(
        gettext_lazy("Email"),
        unique=True,
        error_messages={"unique": gettext_lazy("A user with this email has already registered.")},
    )
    role = models.CharField(gettext_lazy("Role"), max_length=20, choices=Role.choices, default=Role.USER, db_index=True)
    phone = models.CharField(
        gettext_lazy("Phone number"),
        max_length=20,
        blank=True,
        validators=[RegexValidator(r"^[0-9+\-\s()]{7,20}$", gettext_lazy("The phone number is invalid."))],
    )
    email_verified_at = models.DateTimeField(gettext_lazy("Email verification time"), null=True, blank=True)
    bio = models.TextField(gettext_lazy("About me"), blank=True, max_length=500)
    language = models.CharField(gettext_lazy("Language"), max_length=10, blank=True, default="",
                                help_text=gettext_lazy("Your preferred interface language. Leave empty to use the site default."))
    avatar = models.ImageField(gettext_lazy("Profile picture"), upload_to=RandomUploadPath("avatars"), blank=True, validators=[validate_image_file])
    must_change_password = models.BooleanField(gettext_lazy("Must choose a new password at the next sign-in"), default=False,
                                               help_text=gettext_lazy("Until they do, this person can only change their password or sign out."))
    password_changed_at = models.DateTimeField(gettext_lazy("Password last changed"), null=True, blank=True, editable=False)

    objects = UserManager()

    class Meta:
        verbose_name = gettext_lazy("User")
        verbose_name_plural = gettext_lazy("Users")
        ordering = ["-date_joined"]

    def __str__(self):
        return self.get_full_name() or self.username

    # ------------------------------------------------------------------ ذخیره
    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        # ساخت برنامه‌نویسانه کاربر با is_superuser=True (مثلاً createsuperuser) نقش مدیرکل می‌گیرد.
        if self._state.adding and self.is_superuser and self.role == Role.USER:
            self.role = Role.SUPER_ADMIN
        # مدیرکلِ ساخته‌شده با CLI (createsuperuser) مورد اعتماد است؛ ایمیلش تأییدشده فرض می‌شود.
        if self._state.adding and self.role == Role.SUPER_ADMIN and self.email_verified_at is None:
            self.email_verified_at = timezone.now()
        # نقش، منبع حقیقت است.
        self.is_superuser = self.role == Role.SUPER_ADMIN
        self.is_staff = self.role in (Role.SUPER_ADMIN, Role.ADMIN)
        super().save(*args, **kwargs)

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    def mark_password_changed(self):
        """Call after the person chose a new password: clears the "must change" flag and stamps the time. (``set_password`` itself
        does neither, because Django also calls it to re-hash a correct password at sign-in, which is not a change.)"""
        self.must_change_password = False
        self.password_changed_at = timezone.now()
        self.save(update_fields=["password", "must_change_password", "password_changed_at"])

    # ---------------------------------------------------------------- نقش‌ها
    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.username

    @property
    def role_level(self) -> int:
        return ROLE_LEVEL[Role(self.role)]

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.SUPER_ADMIN

    def has_cap(self, capability: str) -> bool:
        if not self.is_active:
            return False
        if capability in ROLE_CAPABILITIES[Role(self.role)]:
            return True
        # مجوزهای اعلام‌شدهٔ افزونه‌های فعال (فقط مدیر/مدیرکل؛ هرگز مجوز هسته)
        from apps.plugins.state import plugin_capability_granted

        return plugin_capability_granted(self.role, capability)

    def can_manage(self, target: "User") -> bool:
        """آیا این کاربر مجاز به تغییر نقش/وضعیت کاربر target است؟"""
        if not self.has_cap("users.manage") or self.pk == target.pk:
            return False
        if self.is_super_admin:
            return True
        return self.role_level > target.role_level

    def assignable_roles(self) -> list[str]:
        """نقش‌هایی که این کاربر می‌تواند به دیگران بدهد."""
        if not self.has_cap("users.manage"):
            return []
        if self.is_super_admin:
            return [r.value for r in Role]
        return [r.value for r in Role if ROLE_LEVEL[r] < self.role_level]


class AccountPolicy(models.Model):
    """How accounts work on this site (a single row). Whether anybody may sign up on their own is ``SiteSettings.registration_open``
    plus ``invitations_enabled`` here; the panel page edits both as one three-way choice (see ``policy.registration_mode``)."""

    DEFAULT_ROLES = (Role.USER, Role.AUTHOR)

    invitations_enabled = models.BooleanField(gettext_lazy("Administrators may invite people"), default=True)
    default_role = models.CharField(gettext_lazy("Role of new sign-ups"), max_length=20, default=Role.USER,
                                    choices=[(r.value, r.label) for r in DEFAULT_ROLES],
                                    help_text=gettext_lazy("The role given to people who sign up on their own. Invitations carry their own role."))
    require_verified_email = models.BooleanField(gettext_lazy("Sign-in requires a verified email"), default=False,
                                                 help_text=gettext_lazy("People must open the link in the verification email before they can sign in. Needs working email (send a test first)."))
    remember_days = models.PositiveSmallIntegerField(gettext_lazy("“Remember me” lasts (days)"), default=30,
                                                     validators=[MinValueValidator(1), MaxValueValidator(365)],
                                                     help_text=gettext_lazy("Without “Remember me”, the sign-in ends when the browser is closed."))
    invitation_days = models.PositiveSmallIntegerField(gettext_lazy("An invitation is valid for (days)"), default=7,
                                                       validators=[MinValueValidator(1), MaxValueValidator(30)])
    require_terms = models.BooleanField(gettext_lazy("New accounts must accept the terms"), default=False)
    terms_page = models.ForeignKey("content.Page", verbose_name=gettext_lazy("Terms of use page"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    privacy_page = models.ForeignKey("content.Page", verbose_name=gettext_lazy("Privacy policy page"), null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    password_min_length = models.PositiveSmallIntegerField(gettext_lazy("Minimum password length"), default=10, validators=[MinValueValidator(8), MaxValueValidator(64)],
                                                           help_text=gettext_lazy("Applies to new passwords only; nobody is forced to change an existing one. A longer password is stronger than a complicated short one."))
    password_check_common = models.BooleanField(gettext_lazy("Refuse common passwords"), default=True,
                                                help_text=gettext_lazy("Checks a built-in list of widely used passwords, including Persian ones. Nothing is sent anywhere."))
    password_check_personal = models.BooleanField(gettext_lazy("Refuse passwords built on the site name or the person’s own details"), default=True)

    class Meta:
        verbose_name = gettext_lazy("Account settings")
        verbose_name_plural = gettext_lazy("Account settings")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "AccountPolicy":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"require_verified_email": bool(getattr(settings, "RAVIX_REQUIRE_EMAIL_VERIFICATION", False))})
        return obj

    @property
    def verification_required(self) -> bool:
        """The environment variable can force it on; the panel switch turns it on for sites that did not set the variable."""
        return bool(self.require_verified_email or getattr(settings, "RAVIX_REQUIRE_EMAIL_VERIFICATION", False))

    @property
    def new_user_role(self) -> str:
        return self.default_role if self.default_role in {r.value for r in self.DEFAULT_ROLES} else Role.USER.value


class SecurityProfile(models.Model):
    """Per-user account facts that are not part of the profile (created when first needed)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="security")
    terms_accepted_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def for_user(cls, user) -> "SecurityProfile":
        obj, _ = cls.objects.get_or_create(user=user)
        return obj


class Invitation(models.Model):
    """An invitation to open an account. Only the SHA-256 of the secret token is stored; the token itself exists only in the emailed link."""

    email = models.EmailField(gettext_lazy("Email"))
    role = models.CharField(gettext_lazy("Role"), max_length=20, choices=Role.choices, default=Role.USER)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        return self.email

    @staticmethod
    def hash_token(token: str) -> str:
        import hashlib

        return hashlib.sha256((token or "").encode()).hexdigest()

    @property
    def state(self) -> str:
        """``used`` | ``revoked`` | ``expired`` | ``pending``."""
        if self.used_at:
            return "used"
        if self.revoked_at:
            return "revoked"
        if self.expires_at <= timezone.now():
            return "expired"
        return "pending"
