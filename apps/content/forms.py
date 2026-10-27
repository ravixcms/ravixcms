from django.utils.translation import gettext, gettext_lazy
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


class LanguageFieldMixin:
    """Turns the ``language`` text field into a choice of the site's languages (default: the site's default language)."""

    def _language_field(self):
        from apps.core.regional import language_choices

        from .models import default_content_language

        current = self.instance.language if self.instance.pk and self.instance.language else default_content_language()
        self.fields["language"] = forms.ChoiceField(
            label=self.fields["language"].label, help_text=self.fields["language"].help_text, choices=language_choices(), required=False, initial=current)
        self.initial.setdefault("language", current)

    def clean_language(self):
        from .models import default_content_language

        return self.cleaned_data.get("language") or (self.instance.language if self.instance.pk else "") or default_content_language()


class PostForm(LanguageFieldMixin, StyledFormMixin, forms.ModelForm):
    publish_at = forms.DateTimeField(
        label=gettext_lazy("Publish time"),
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "dir": "ltr"}, format=DATETIME_LOCAL),
        input_formats=[DATETIME_LOCAL, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text=gettext_lazy("Required only for the “Scheduled” status; the post is treated as published automatically at this time."),
    )

    class Meta:
        model = Post
        fields = ("title", "slug", "excerpt", "body", "featured_image", "language", "status", "publish_at", "author", "categories", "tags",
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
        self._language_field()
        self.can_publish = user.has_cap("content.publish")
        self.fields["status"].initial = self.instance.status if self.instance.pk else PublishStatus.DRAFT
        self.fields["categories"].queryset = Category.objects.all()
        self.fields["tags"].queryset = Tag.objects.all()
        if self.can_publish:
            eligible = Q(is_active=True, role__in=[Role.AUTHOR, Role.ADMIN, Role.SUPER_ADMIN])
            if self.instance.author_id:
                eligible |= Q(pk=self.instance.author_id)  # نویسنده فعلی همیشه قابل انتخاب می‌ماند
            self.fields["author"] = UserChoiceField(
                label=gettext("Author"),
                queryset=User.objects.filter(eligible),
                required=True,
                initial=self.instance.author_id or user.pk,
                widget=forms.Select(attrs={"class": "input"}),
            )
        else:
            del self.fields["author"]
            del self.fields["publish_at"]
            self.fields["status"].choices = [(PublishStatus.DRAFT, PublishStatus.DRAFT.label)]
            self.fields["status"].help_text = gettext("Publishing is done by an administrator; your post will be saved as a draft.")

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        publish_at = cleaned.get("publish_at")
        if not self.can_publish:
            return cleaned
        now = timezone.now()
        if status == PublishStatus.SCHEDULED:
            if not publish_at:
                self.add_error("publish_at", gettext("For a scheduled post, specify the publish time."))
            else:
                changed = self.instance.status != PublishStatus.SCHEDULED or self.instance.publish_at != publish_at
                if changed and publish_at <= now:
                    self.add_error("publish_at", gettext("The publish time must be in the future."))
        elif status == PublishStatus.PUBLISHED and publish_at and publish_at > now:
            # «منتشرشده» یعنی همین حالا زنده است؛ انتشار در آینده فقط با وضعیت زمان‌بندی‌شده معنا دارد.
            self.add_error("publish_at", gettext("To publish at a future time, choose the “Scheduled” status."))
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


class PageForm(LanguageFieldMixin, StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Page
        fields = ("title", "slug", "body", "language", "status", "seo_title", "seo_description")
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
        self._language_field()

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        if slug in RESERVED_PAGE_SLUGS:
            raise forms.ValidationError(gettext("This slug is reserved for system sections of the site; choose another slug."))
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
        self.fields["parent"].empty_label = gettext("— No parent —")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError(gettext("The name cannot be empty."))
        return name


class TagForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Tag
        fields = ("name", "slug")
        widgets = {"slug": forms.TextInput(attrs={"dir": "ltr"})}

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError(gettext("The name cannot be empty."))
        return name


class RedirectForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Redirect
        fields = ("old_path", "new_path", "status_code", "note")
        widgets = {
            "old_path": forms.TextInput(attrs={"dir": "ltr"}),
            "new_path": forms.TextInput(attrs={"dir": "ltr"}),
        }
