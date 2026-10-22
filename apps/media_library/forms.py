from django import forms
from django.core.exceptions import ValidationError
from PIL import Image

from apps.core.forms import StyledFormMixin

from .models import Media
from .validators import MAX_FILES_PER_UPLOAD, MIME_TYPES, get_extension, validate_media_file


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            if not data:
                return single(None, initial)  # خطای «الزامی بودن» را برمی‌گرداند
            return [single(d, initial) for d in data]
        return [single(data, initial)] if data else single(data, initial)


class MediaUploadForm(StyledFormMixin, forms.Form):
    files = MultipleFileField(label="فایل‌ها", help_text=f"حداکثر {MAX_FILES_PER_UPLOAD} فایل در هر بار بارگذاری.")
    title = forms.CharField(label="عنوان", max_length=200, required=False, help_text="فقط هنگام بارگذاری یک فایل اعمال می‌شود.")
    alt_text = forms.CharField(label="متن جایگزین (Alt Text)", max_length=300, required=False,
                               help_text="فقط هنگام بارگذاری یک تصویر اعمال می‌شود؛ برای چند فایل بعداً جداگانه ثبت کنید.")

    def clean_files(self):
        files = self.cleaned_data["files"]
        if len(files) > MAX_FILES_PER_UPLOAD:
            raise ValidationError(f"در هر بار حداکثر {MAX_FILES_PER_UPLOAD} فایل می‌توانید بارگذاری کنید.")
        errors, kinds = [], {}
        for f in files:
            try:
                kinds[f.name] = validate_media_file(f)
            except ValidationError as exc:
                errors.append(ValidationError("«%(name)s»: %(msg)s", params={"name": f.name, "msg": exc.messages[0]}))
        if errors:
            raise ValidationError(errors)  # همه یا هیچ: اگر یک فایل نامعتبر باشد چیزی ذخیره نمی‌شود
        self.kinds = kinds
        return files

    def save(self, user):
        files = self.cleaned_data["files"]
        single = len(files) == 1
        created = []
        for f in files:
            kind = self.kinds[f.name]
            ext = get_extension(f.name)
            media = Media(
                file=f,
                original_name=f.name[:255],
                kind=kind,
                mime_type=MIME_TYPES[ext],
                size=f.size,
                uploaded_by=user,
                title=self.cleaned_data["title"] if single else "",
                alt_text=self.cleaned_data["alt_text"] if single and kind == "image" else "",
            )
            if kind == "image":
                f.seek(0)
                with Image.open(f) as img:
                    media.width, media.height = img.size
                f.seek(0)
            media.save()
            created.append(media)
        return created


class MediaEditForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Media
        fields = ("title", "alt_text")
