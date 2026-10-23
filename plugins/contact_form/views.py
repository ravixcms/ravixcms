from django.utils.translation import gettext
import logging

from django.contrib import messages
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404

from .forms import ContactForm
from .models import ContactMessage

logger = logging.getLogger(__name__)



def _translated(text):
    """Translate the stored text (e.g. the manifest default); custom text is not in the catalog and stays as written."""
    return gettext(text) if text else text


def form(request, api):
    """صفحهٔ عمومی فرم تماس (GET نمایش، POST ثبت). CSRF و ... را هسته اعمال می‌کند."""
    sent = request.GET.get("sent") == "1"
    f = ContactForm(request.POST or None)
    if request.method == "POST":
        if not api.rate_limit(request, "submit", api.setting("max_per_hour", 5), 3600):
            response = api.render_public(request, "form.html", {"form": f, "limited": True}, title=gettext("Contact us"), status=429)
            response["Cache-Control"] = "private, no-store"
            return response
        if f.is_valid():
            data = f.cleaned_data
            if data["website"]:  # ربات: بدون ذخیره، پاسخ موفق ظاهری
                from django.shortcuts import redirect

                return redirect(api.public_url() + "?sent=1")
            msg = ContactMessage.objects.create(name=data["name"], email=data["email"], subject=data["subject"], message=data["message"])
            notify = api.setting("notify_email", "")
            if notify:
                try:
                    # The reader is the site owner, not the visitor who filled in the form: write the mail in the owner's language.
                    with api.use_language(api.recipient_language(notify)):
                        subject = (gettext("New contact form message: %(value)s") % {"value": (msg.subject or msg.name)})[:150].replace("\n", " ")
                        body = gettext("From: %(name)s <%(email)s>\n\n%(message)s") % {"name": msg.name, "email": msg.email, "message": msg.message}
                    send_mail(subject, body, None, [notify], fail_silently=False)
                except Exception as exc:  # noqa: BLE001 - خطای ایمیل نباید ثبت پیام را خراب کند
                    api.log((gettext("Sending the notification email failed: %(exc)s") % {"exc": exc}), level="warning")
            from django.shortcuts import redirect

            return redirect(api.public_url() + "?sent=1")
    response = api.render_public(request, "form.html", {"form": f, "sent": sent, "intro": api.setting("intro", ""), "success": _translated(api.setting("success_message", ""))},
                                 title=gettext("Contact us"), status=400 if request.method == "POST" else 200)
    response["Cache-Control"] = "private, no-store"
    return response


def inbox(request, api):
    """صندوق پیام‌ها در پنل (مجوز contact_form.view را هسته پیش از فراخوانی بررسی کرده است)."""
    if request.method == "POST":
        msg = get_object_or_404(ContactMessage, pk=request.POST.get("id") or 0)
        action = request.POST.get("action")
        if action == "delete":
            msg.delete()
            messages.success(request, gettext("The message was deleted."))
        elif action in ("read", "unread"):
            msg.is_read = action == "read"
            msg.save(update_fields=["is_read"])
        api.audit(request, (gettext("%(action)s contact message #%(POST)s") % {"action": action, "POST": request.POST.get('id')}))
        return api.redirect_admin("inbox")
    qs = ContactMessage.objects.all()
    if request.GET.get("unread") == "1":
        qs = qs.filter(is_read=False)
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return api.render_admin(request, "inbox.html", {"page_obj": page, "paginator": page.paginator, "is_paginated": page.has_other_pages(),
                                                    "unread_count": ContactMessage.objects.filter(is_read=False).count(),
                                                    "only_unread": request.GET.get("unread") == "1"}, title=gettext("Contact messages"))
