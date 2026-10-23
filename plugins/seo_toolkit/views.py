from django.utils.translation import gettext
from django.contrib import messages
from django.http import Http404

from apps.content.models import Page, Post

from .forms import SeoMetaForm
from .models import SeoMeta


def _items():
    metas = {(m.kind, m.object_id): m for m in SeoMeta.objects.all()}
    rows = []
    for kind, label, qs in (("page", gettext("Page"), Page.objects.all()), ("post", gettext("Post"), Post.objects.all())):
        for obj in qs.order_by("-updated_at")[:200]:
            m = metas.get((kind, obj.pk))
            rows.append({"kind": kind, "kind_label": label, "id": obj.pk, "title": obj.title, "meta": m})
    return rows


def overview(request, api):
    return api.render_admin(request, "overview.html", {"rows": _items()}, title=gettext("Page SEO"))


def edit(request, api):
    kind = request.GET.get("kind") or request.POST.get("kind")
    try:
        obj_id = int(request.GET.get("id") or request.POST.get("id") or 0)
    except ValueError:
        raise Http404
    model = {"page": Page, "post": Post}.get(kind)
    if model is None:
        raise Http404
    obj = model.objects.filter(pk=obj_id).first()
    if obj is None:
        raise Http404
    meta = SeoMeta.objects.filter(kind=kind, object_id=obj_id).first() or SeoMeta(kind=kind, object_id=obj_id)
    form = SeoMetaForm(request.POST or None, instance=meta)
    if request.method == "POST" and form.is_valid():
        form.save()
        api.audit(request, (gettext("Change SEO of %(kind)s #%(obj_id)s") % {"kind": kind, "obj_id": obj_id}))
        messages.success(request, gettext("SEO settings were saved."))
        return api.redirect_admin("overview")
    return api.render_admin(request, "edit.html", {"form": form, "obj": obj, "kind": kind, "obj_id": obj_id},
                            title=(gettext("SEO: %(title)s") % {"title": obj.title}), status=400 if form.errors else 200)
