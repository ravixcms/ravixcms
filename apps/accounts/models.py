from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.validators import RegexValidator
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
        "ایمیل",
        unique=True,
        error_messages={"unique": "کاربری با این ایمیل قبلاً ثبت‌نام کرده است."},
    )
    role = models.CharField("نقش", max_length=20, choices=Role.choices, default=Role.USER, db_index=True)
    phone = models.CharField(
        "شماره تلفن",
        max_length=20,
        blank=True,
        validators=[RegexValidator(r"^[0-9+\-\s()]{7,20}$", "شماره تلفن معتبر نیست.")],
    )
    email_verified_at = models.DateTimeField("زمان تأیید ایمیل", null=True, blank=True)
    bio = models.TextField("درباره من", blank=True, max_length=500)
    avatar = models.ImageField("تصویر پروفایل", upload_to=RandomUploadPath("avatars"), blank=True, validators=[validate_image_file])

    objects = UserManager()

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"
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
