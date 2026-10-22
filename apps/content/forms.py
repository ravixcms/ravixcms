from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from apps.core.utils import RESERVED_PAGE_SLUGS

from apps.accounts.permissions import Role
from apps.core.forms import StyledFormMixin

from .models import Category, Page, Post, PublishStatus, Redirect, Tag

User = get_user_model()

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class UserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.display_name} (@{obj.username})"


class PostForm(StyledFormMixin, forms.ModelForm):
    publish_at = forms.DateTimeField(
        label="زمان انتشار",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "dir": "ltr"}, format=DATETIME_LOCAL),
        input_formats=[DATETIME_LOCAL, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text="فقط برای وضعیت «زمان‌بندی‌شده» لازم است؛ نوشته در این زمان به‌صورت خودکار منتشر‌شده حساب می‌شود.",
    )

    class Meta:
        model = Post
        fields = ("title", "slug", "excerpt", "body", "featured_image", "status", "publish_at", "author", "categories", "tags",
                  "seo_title", "seo_description", "noindex", "comments_open")
        widgets = {
            "slug": forms.TextInput(attrs={"dir": "ltr"}),
            "seo_title": forms.TextInput(attrs={"data-counter": "60"}),
            "seo_description": forms.Textarea(attrs={"rows": 3, "data-counter": "160"}),
            "excerpt": forms.Textarea(attrs={"rows": 3}),
            "body": forms.Textarea(attrs={"rows": 16}),
            "status": forms.RadioSelect,
            "categories": forms.CheckboxSelectMultiple,
            "tags": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.can_publish = user.has_cap("content.publish")
        self.fields["status"].initial = self.instance.status if self.instance.pk else PublishStatus.DRAFT
        self.fields["categories"].queryset = Category.objects.all()
        self.fields["tags"].queryset = Tag.objects.all()
        if self.can_publish:
            eligible = Q(is_active=True, role__in=[Role.AUTHOR, Role.ADMIN, Role.SUPER_ADMIN])
            if self.instance.author_id:
                eligible |= Q(pk=self.instance.author_id)  # نویسنده فعلی همیشه قابل انتخاب می‌ماند
            self.fields["author"] = UserChoiceField(
                label="نویسنده",
                queryset=User.objects.filter(eligible),
                required=True,
                initial=self.instance.author_id or user.pk,
                widget=forms.Select(attrs={"class": "input"}),
            )
        else:
            del self.fields["author"]
            del self.fields["publish_at"]
            self.fields["status"].choices = [(PublishStatus.DRAFT, PublishStatus.DRAFT.label)]
            self.fields["status"].help_text = "انتشار نوشته توسط مدیر انجام می‌شود؛ نوشته شما به‌صورت پیش‌نویس ذخیره می‌شود."

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        publish_at = cleaned.get("publish_at")
        if not self.can_publish:
            return cleaned
        now = timezone.now()
        if status == PublishStatus.SCHEDULED:
            if not publish_at:
                self.add_error("publish_at", "برای نوشته زمان‌بندی‌شده، زمان انتشار را مشخص کنید.")
            else:
                changed = self.instance.status != PublishStatus.SCHEDULED or self.instance.publish_at != publish_at
                if changed and publish_at <= now:
                    self.add_error("publish_at", "زمان انتشار باید در آینده باشد.")
        elif status == PublishStatus.PUBLISHED and publish_at and publish_at > now:
            # «منتشرشده» یعنی همین حالا زنده است؛ انتشار در آینده فقط با وضعیت زمان‌بندی‌شده معنا دارد.
            self.add_error("publish_at", "برای انتشار در زمان آینده، وضعیت «زمان‌بندی‌شده» را انتخاب کنید.")
        return cleaned

    def save(self, commit=True):
        post = super().save(commit=False)
        if post.pk and self.has_changed():
            post.is_sample = False  # ویرایش‌شده یعنی دیگر «نمونه» نیست
        if not self.can_publish:
            if not post.pk:
                post.author = self.user
            post.status = PublishStatus.DRAFT
        if commit:
            post.save()
            self.save_m2m()
        return post


class PageForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Page
        fields = ("title", "slug", "body", "status", "seo_title", "seo_description")
        widgets = {
            "slug": forms.TextInput(attrs={"dir": "ltr"}),
            "body": forms.Textarea(attrs={"rows": 16}),
            "status": forms.RadioSelect,
            "seo_title": forms.TextInput(attrs={"data-counter": "60"}),
            "seo_description": forms.Textarea(attrs={"rows": 3, "data-counter": "160"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        if slug in RESERVED_PAGE_SLUGS:
            raise forms.ValidationError("این نامک برای بخش‌های سیستمی سایت رزرو شده است؛ نامک دیگری انتخاب کنید.")
        return self.cleaned_data.get("slug")

    def save(self, commit=True):
        page = super().save(commit=False)
        if page.pk and self.has_changed():
            page.is_sample = False
        if not page.pk:
            page.author = self.user
        if commit:
            page.save()
        return page


class CategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "slug", "description", "parent")
        widgets = {"slug": forms.TextInput(attrs={"dir": "ltr"}), "description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Category.objects.all()
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk).exclude(pk__in=self.instance.descendant_ids())
        self.fields["parent"].queryset = qs
        self.fields["parent"].empty_label = "— بدون والد —"

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("نام نمی‌تواند خالی باشد.")
        return name


class TagForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Tag
        fields = ("name", "slug")
        widgets = {"slug": forms.TextInput(attrs={"dir": "ltr"})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("نام نمی‌تواند خالی باشد.")
        return name


class RedirectForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Redirect
        fields = ("old_path", "new_path", "status_code", "note")
        widgets = {
            "old_path": forms.TextInput(attrs={"dir": "ltr"}),
            "new_path": forms.TextInput(attrs={"dir": "ltr"}),
        }
