"""مدیریت سایت‌ها (فقط مدیرکل) و تعویض سایت فعال پنل."""
from django.contrib import messages
from apps.core.params import safe_id
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views import View

from django.contrib.auth import get_user_model

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.audit.models import AuditLog
from apps.audit.services import log_event

from .access import SESSION_KEY, accessible_sites, create_site
from .models import Site, SiteDomain, SiteMember


class SitesAdmin(CapabilityRequiredMixin):
    required_capability = "sites.manage"


class SiteListView(SitesAdmin, View):
    def get(self, request):
        return render(request, "sites/list.html", {"sites": Site.objects.select_related("owner").prefetch_related("domains")})

    def post(self, request):
        name = request.POST.get("name", "").strip()[:100]
        slug = slugify(request.POST.get("slug", "") or name)[:50]
        if len(name) < 2 or not slug:
            messages.error(request, "نام و شناسهٔ معتبر لازم است.")
        else:
            try:
                site = create_site(name, slug, request.user)
            except IntegrityError:
                messages.error(request, "این شناسه قبلاً استفاده شده است.")
            else:
                log_event(request, AuditLog.Action.CREATE, site, f"سایت «{name}» ساخته شد")
                messages.success(request, "سایت ساخته شد.")
        return redirect("sites:list")


class SiteDetailView(SitesAdmin, View):
    def _site(self, pk):
        return get_object_or_404(Site.objects.select_related("owner"), pk=pk)

    def get(self, request, pk):
        site = self._site(pk)
        return render(request, "sites/detail.html", {"site": site, "domains": site.domains.all(), "members": site.members.select_related("user"),
                                                     "roles": SiteMember.Role.choices})

    def post(self, request, pk):
        site, action = self._site(pk), request.POST.get("action")
        U = get_user_model()
        try:
            if action == "add_domain":
                d = SiteDomain(site=site, domain=request.POST.get("domain", ""))
                d.full_clean(exclude=["site"])
                d.save()
            elif action == "remove_domain":
                site.domains.filter(pk=safe_id(request.POST.get("id")) or 0).delete()
            elif action == "add_member":
                user = U.objects.filter(username=request.POST.get("username", "").strip()).first()
                role = request.POST.get("role")
                if user is None or role not in SiteMember.Role.values:
                    raise ValidationError("کاربر یا نقش نامعتبر است.")
                SiteMember.objects.update_or_create(site=site, user=user, defaults={"role": role})
            elif action == "remove_member":
                m = site.members.filter(pk=safe_id(request.POST.get("id")) or 0).first()
                if m and m.user_id == site.owner_id:
                    raise ValidationError("مالک سایت را نمی‌توان حذف کرد.")
                if m:
                    m.delete()
            elif action == "toggle_active" and not site.is_default:
                site.is_active = not site.is_active
                site.save(update_fields=["is_active"])
            else:
                raise Http404
        except ValidationError as exc:
            messages.error(request, "؛ ".join(exc.messages))
        except IntegrityError:
            messages.error(request, "این دامنه قبلاً ثبت شده است.")
        else:
            log_event(request, AuditLog.Action.UPDATE, site, f"تغییر سایت: {action}")
            messages.success(request, "ذخیره شد.")
        return redirect("sites:detail", pk=pk)


class SwitchSiteView(View):
    http_method_names = ["post"]

    def post(self, request):
        if not request.user.is_authenticated:
            raise PermissionDenied
        site = accessible_sites(request.user).filter(pk=request.POST.get("site") or 0).first()
        if site is None:
            raise PermissionDenied  # سایتی که عضوش نیست قابل انتخاب نیست
        request.session[SESSION_KEY] = site.pk
        return redirect(request.POST.get("next") if (request.POST.get("next", "").startswith("/panel/")) else "dashboard:home")
