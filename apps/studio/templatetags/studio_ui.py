from django import template
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext

from apps.core import modules

register = template.Library()


@register.simple_tag(takes_context=True)
def studio_edit_button(context, kind, obj, css="btn btn-light"):
    """The «Edit with Ravix Studio» button of a page or post; empty when the module is off or the user may not use it."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if not obj or not getattr(obj, "pk", None) or user is None or not user.is_authenticated or not modules.is_enabled("studio"):
        return ""
    if not user.has_cap("studio.edit"):
        return ""
    if kind == "post" and not obj.user_can_edit(user):  # authors design their own drafts only
        return ""
    return format_html('<a href="{}" class="{}" data-studio-edit>{}</a>', reverse("studio:editor", args=[kind, obj.pk]), css, gettext("Edit with Ravix Studio"))
