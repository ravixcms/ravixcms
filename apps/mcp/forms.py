import re

from django import forms
from django.utils.translation import gettext_lazy as _

from . import scopes as sc
from .models import McpSettings

_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_ORIGIN = re.compile(r"^https?://[a-z0-9.\-]+(:\d{1,5})?$", re.I)


def _scope_field(label, help_text=""):
    return forms.MultipleChoiceField(
        label=label, required=False, help_text=help_text, widget=forms.CheckboxSelectMultiple,
        choices=[(s, sc.LABELS[s]) for s in sc.SCOPES],
    )


class McpSettingsForm(forms.ModelForm):
    admin_scopes = _scope_field(_("What a manager may give to an app"))
    author_scopes = _scope_field(_("What an author may give to an app"), _("Nothing is ticked by default: authors cannot connect apps until you allow it."))

    class Meta:
        model = McpSettings
        fields = ["enabled", "unknown_apps", "trusted_hosts", "access_token_minutes", "rate_per_minute", "write_per_minute", "allowed_origins"]
        widgets = {"trusted_hosts": forms.Textarea(attrs={"rows": 3, "dir": "ltr"}), "allowed_origins": forms.Textarea(attrs={"rows": 3, "dir": "ltr"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.accounts.permissions import Role

        self.fields["admin_scopes"].initial = self.instance.scopes_for_role(Role.ADMIN)
        self.fields["author_scopes"].initial = self.instance.scopes_for_role(Role.AUTHOR)
        if McpSettings.multisite_blocks():
            self.fields["enabled"].disabled = True
            self.fields["enabled"].help_text = _("Several sites share this installation, so Ravix MCP cannot be switched on: posts and pages are not separated per site yet.")

    def clean_trusted_hosts(self):
        hosts = [h.strip().lower() for h in self.cleaned_data["trusted_hosts"].splitlines() if h.strip()]
        for h in hosts:
            if not _HOST.match(h):
                raise forms.ValidationError(_("“%(value)s” is not a host name such as app.example.com."), params={"value": h[:60]})
        return "\n".join(dict.fromkeys(hosts))

    def clean_allowed_origins(self):
        origins = [o.strip().rstrip("/") for o in self.cleaned_data["allowed_origins"].splitlines() if o.strip()]
        for o in origins:
            if not _ORIGIN.match(o):
                raise forms.ValidationError(_("“%(value)s” is not an origin such as https://app.example.com."), params={"value": o[:60]})
        return "\n".join(dict.fromkeys(origins))

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.admin_scopes = [s for s in sc.SCOPES if s in set(self.cleaned_data["admin_scopes"])]
        obj.author_scopes = [s for s in sc.SCOPES if s in set(self.cleaned_data["author_scopes"])]
        if McpSettings.multisite_blocks():
            obj.enabled = False
        if commit:
            obj.save()
        return obj
