from django import forms

from .settings_schema import SettingField


def build_settings_form(schema, data=None, initial=None):
    """از اسکیمای manifest فرم می‌سازد؛ اعتبارسنجی نهایی همیشه با SettingField.clean (یک منبع حقیقت) انجام می‌شود."""
    fields = {}
    for f in schema:
        kw = {"label": f.label, "help_text": f.help, "required": False}
        if f.type == "bool":
            fields[f.key] = forms.BooleanField(widget=forms.CheckboxInput(attrs={"class": "check"}), **kw)
        elif f.type == "textarea":
            fields[f.key] = forms.CharField(widget=forms.Textarea(attrs={"class": "input", "rows": 4, "maxlength": f.max_len}), **kw)
        elif f.type == "int":
            fields[f.key] = forms.CharField(widget=forms.NumberInput(attrs={"class": "input ltr", "min": f.min, "max": f.max}), **kw)
        elif f.type == "choice":
            fields[f.key] = forms.ChoiceField(choices=list(f.choices), widget=forms.Select(attrs={"class": "input"}), **kw)
        else:
            attrs = {"class": "input" + (" ltr" if f.type in ("email", "url") else ""), "maxlength": f.max_len}
            fields[f.key] = forms.CharField(widget=forms.TextInput(attrs=attrs), **kw)
    form_cls = type("PluginSettingsForm", (forms.Form,), fields)
    form = form_cls(data=data, initial=initial)
    form.schema = list(schema)
    return form


def validate_form(form: forms.Form):
    """(پاک‌شده، خطاها) با اسکیما؛ خطاها روی فرم هم گذاشته می‌شوند."""
    from .settings_schema import clean_all

    raw = {}
    for f in form.schema:
        if f.type == "bool":
            raw[f.key] = form.data.get(f.key) in ("on", "true", "1", "True")
        else:
            raw[f.key] = form.data.get(f.key, "")
    clean, errors = clean_all(form.schema, raw)
    for key, msg in errors.items():
        form.add_error(key, msg)
    return clean, errors


__all__ = ["build_settings_form", "validate_form", "SettingField"]
