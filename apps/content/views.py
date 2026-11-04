from django.utils.translation import gettext, gettext_lazy
from django.conf import settings
from django.core.exceptions import PermissionDenied
from apps.core.regional import language_choices
from apps.core.params import safe_id
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from apps.accounts.mixins import CapabilityRequiredMixin
from apps.core.crud import (
    AuditedCreateView,
    AuditedDeleteView,
    AuditedUpdateView,
    QueryStringMixin,
)
from apps.core.markup import render_markdown

from django.shortcuts import get_object_or_404
from django.urls import reverse

from . import translations
from .forms import CategoryForm, PageForm, PostForm, RedirectForm, TagForm
from .models import Category, Page, Post, PublishStatus, Redirect, Tag



# ============================================================ نوشته‌ها
class PostListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = "content.write"
    template_name = "content/post_list.html"
    context_object_name = "posts"
    paginate_by = 15

    def get_queryset(self):
        user = self.request.user
        qs = Post.objects.select_related("author").prefetch_related("categories", "tags")
        if not user.has_cap("content.publish"):
            qs = qs.filter(author=user)  # نویسنده فقط نوشته‌های خودش را می‌بیند
        g = self.request.GET
        q = g.get("q", "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(body__icontains=q))
        now = timezone.now()
        status = g.get("status", "")
        if status == "draft":
            qs = qs.filter(status=PublishStatus.DRAFT)
        elif status == "published":
            qs = qs.filter(Q(status=PublishStatus.PUBLISHED) | Q(status=PublishStatus.SCHEDULED, publish_at__lte=now))
        elif status == "scheduled":
            qs = qs.filter(status=PublishStatus.SCHEDULED, publish_at__gt=now)
        if g.get("language", "") in dict(settings.LANGUAGES):
            qs = qs.filter(language=g["language"])
        if safe_id(g.get("category")) is not None:
            qs = qs.filter(categories__pk=safe_id(g.get("category")))
        if safe_id(g.get("tag")) is not None:
            qs = qs.filter(tags__pk=safe_id(g.get("tag")))
        if safe_id(g.get("author")) is not None and user.has_cap("content.publish"):
            qs = qs.filter(author__pk=safe_id(g.get("author")))
        return qs.distinct()

    def get_context_data(self, **kwargs):
        from django.contrib.auth import get_user_model

        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        g = self.request.GET
        ctx.update(
            q=g.get("q", ""),
            selected_status=g.get("status", ""),
            selected_language=g.get("language", ""),
            languages=language_choices(),
            selected_category=g.get("category", ""),
            selected_tag=g.get("tag", ""),
            selected_author=g.get("author", ""),
            categories=Category.objects.all(),
            tags=Tag.objects.all(),
            show_author_filter=user.has_cap("content.publish"),
            editable_ids={p.pk for p in ctx["posts"] if p.user_can_edit(user)},
        )
        if ctx["show_author_filter"]:
            ctx["authors"] = get_user_model().objects.filter(posts__isnull=False).distinct()
        return ctx


class PostDetailView(CapabilityRequiredMixin, DetailView):
    required_capability = "content.write"
    template_name = "content/post_detail.html"
    context_object_name = "post"

    def get_queryset(self):
        return Post.objects.select_related("author").prefetch_related("categories", "tags")

    def get_object(self, queryset=None):
        post = super().get_object(queryset)
        if not post.user_can_view(self.request.user):
            raise PermissionDenied
        return post

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["can_edit"] = self.object.user_can_edit(self.request.user)
        return ctx


class PostFormMixin:
    model = Post
    form_class = PostForm
    object_label = gettext_lazy("Post")
    list_url_name = "content:post_list"
    required_capability = "content.write"
    pass_user_to_form = True
    template_name = "content/post_form.html"

    def log_publish_transition(self, before_status):
        post = self.object
        if post.status in (PublishStatus.PUBLISHED, PublishStatus.SCHEDULED) and before_status != post.status:
            label = post.get_status_display()
            self.audit("publish", post, (gettext("The status of post “%(post)s” was changed to “%(label)s”.") % {"post": post, "label": label}))


class TranslationContextMixin:
    """The "Translations" card of the edit form, and the starting point of "create a translation"."""

    def get_initial(self):
        initial = super().get_initial()
        origin = self.translation_origin()
        if origin is not None:
            initial["translation_of"] = origin.pk
            language = self.request.GET.get("language", "")
            if language in dict(settings.LANGUAGES) and language in dict(translations.free_languages(origin)):
                initial["language"] = language
        return initial

    def translation_origin(self):
        if not translations.enabled():
            return None
        try:
            pk = int(self.request.GET.get("translation_of", ""))
        except ValueError:
            return None
        origin = self.model.objects.filter(pk=pk).first()
        if origin is None:
            return None
        check = getattr(origin, "user_can_edit", None)
        return origin if (check(self.request.user) if check else True) else None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["translations_enabled"] = translations.enabled()
        if not ctx["translations_enabled"]:
            return ctx
        obj = ctx.get("object") if getattr(self, "object", None) is not None and self.object.pk else None
        origin = obj or self.translation_origin()
        ctx["translation_origin"] = self.translation_origin() if obj is None else None
        if obj is not None:
            ctx["translation_rows"] = [{"obj": m, "language": translations.language_name(m.language), "current": m.pk == obj.pk,
                                        "edit_url": reverse(f"content:{self.kind}_update", args=[m.pk])} for m in translations.members(obj)]
            ctx["translation_missing"] = [{"code": code, "name": name, "url": f"{reverse(f'content:{self.kind}_create')}?translation_of={obj.pk}&language={code}"}
                                          for code, name in translations.free_languages(obj)]
            ctx["translation_link_url"] = reverse(f"content:{self.kind}_translation_link", args=[obj.pk])
            ctx["translation_unlink_url"] = reverse(f"content:{self.kind}_translation_unlink", args=[obj.pk])
        elif origin is not None:
            ctx["translation_origin_title"] = origin.title
            ctx["translation_origin_language"] = translations.language_name(origin.language)
        return ctx


class PostCreateView(TranslationContextMixin, PostFormMixin, AuditedCreateView):
    kind = "post"

    def form_valid(self, form):
        response = super().form_valid(form)
        self.log_publish_transition(None)
        return response


class PostUpdateView(TranslationContextMixin, PostFormMixin, AuditedUpdateView):
    kind = "post"

    def check_object_permission(self, obj):
        return obj.user_can_edit(self.request.user)

    def form_valid(self, form):
        before = Post.objects.filter(pk=self.get_object().pk).values_list("status", flat=True).first()
        response = super().form_valid(form)
        self.log_publish_transition(before)
        return response


class PostDeleteView(AuditedDeleteView):
    model = Post
    object_label = gettext_lazy("Post")
    list_url_name = "content:post_list"
    required_capability = "content.write"

    def check_object_permission(self, obj):
        return obj.user_can_edit(self.request.user)


# ============================================================ صفحات
class PageListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = "pages.manage"
    template_name = "content/page_list.html"
    context_object_name = "pages"
    paginate_by = 15

    def get_queryset(self):
        qs = Page.objects.select_related("author")
        q = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "")
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(slug__icontains=q) | Q(body__icontains=q))
        if status in (PublishStatus.DRAFT, PublishStatus.PUBLISHED):
            qs = qs.filter(status=status)
        if self.request.GET.get("language", "") in dict(settings.LANGUAGES):
            qs = qs.filter(language=self.request.GET["language"])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["selected_language"] = self.request.GET.get("language", "")
        ctx["languages"] = language_choices()
        return ctx


class PageDetailView(CapabilityRequiredMixin, DetailView):
    required_capability = "pages.manage"
    model = Page
    template_name = "content/page_detail.html"
    context_object_name = "page"


class PageFormMixin:
    model = Page
    form_class = PageForm
    object_label = gettext_lazy("Page")
    list_url_name = "content:page_list"
    required_capability = "pages.manage"
    pass_user_to_form = True
    template_name = "content/page_form.html"


class PageCreateView(TranslationContextMixin, PageFormMixin, AuditedCreateView):
    kind = "page"


class PageUpdateView(TranslationContextMixin, PageFormMixin, AuditedUpdateView):
    kind = "page"


class PageDeleteView(AuditedDeleteView):
    model = Page
    object_label = gettext_lazy("Page")
    list_url_name = "content:page_list"
    required_capability = "pages.manage"


# ============================================================ دسته‌بندی‌ها و برچسب‌ها
class CategoryListView(CapabilityRequiredMixin, ListView):
    required_capability = "taxonomy.manage"
    template_name = "content/category_list.html"
    context_object_name = "categories"

    def get_queryset(self):
        # annotate(Count) ترتیب Meta را حذف می‌کند؛ پس ترتیب باید صریح باشد
        return Category.objects.select_related("parent").annotate(post_count=Count("posts")).order_by("name")


class CategoryFormMixin:
    model = Category
    form_class = CategoryForm
    object_label = gettext_lazy("Content category")
    list_url_name = "content:category_list"
    required_capability = "taxonomy.manage"


class CategoryCreateView(CategoryFormMixin, AuditedCreateView):
    pass


class CategoryUpdateView(CategoryFormMixin, AuditedUpdateView):
    pass


class CategoryDeleteView(AuditedDeleteView):
    model = Category
    object_label = gettext_lazy("Content category")
    list_url_name = "content:category_list"
    required_capability = "taxonomy.manage"


class TagListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = "taxonomy.manage"
    template_name = "content/tag_list.html"
    context_object_name = "tags"
    paginate_by = 30

    def get_queryset(self):
        qs = Tag.objects.annotate(post_count=Count("posts")).order_by("name")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class TagFormMixin:
    model = Tag
    form_class = TagForm
    object_label = gettext_lazy("Tag")
    list_url_name = "content:tag_list"
    required_capability = "taxonomy.manage"


class TagCreateView(TagFormMixin, AuditedCreateView):
    pass


class TagUpdateView(TagFormMixin, AuditedUpdateView):
    pass


class TagDeleteView(AuditedDeleteView):
    model = Tag
    object_label = gettext_lazy("Tag")
    list_url_name = "content:tag_list"
    required_capability = "taxonomy.manage"


# ============================================================ ریدایرکت‌ها
class RedirectListView(CapabilityRequiredMixin, QueryStringMixin, ListView):
    required_capability = "redirects.manage"
    template_name = "content/redirect_list.html"
    context_object_name = "redirects"
    paginate_by = 30

    def get_queryset(self):
        qs = Redirect.objects.all()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(old_path__icontains=q) | Q(new_path__icontains=q) | Q(note__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class RedirectFormMixin:
    model = Redirect
    form_class = RedirectForm
    object_label = gettext_lazy("Redirect")
    list_url_name = "content:redirect_list"
    required_capability = "redirects.manage"


class RedirectCreateView(RedirectFormMixin, AuditedCreateView):
    pass


class RedirectUpdateView(RedirectFormMixin, AuditedUpdateView):
    pass


class RedirectDeleteView(AuditedDeleteView):
    model = Redirect
    object_label = gettext_lazy("Redirect")
    list_url_name = "content:redirect_list"
    required_capability = "redirects.manage"


# ============================================================ پیش‌نمایش Markdown
class MarkdownPreviewView(CapabilityRequiredMixin, View):
    """پیش‌نمایش زنده متن؛ خروجی همان رندر امنی است که هنگام ذخیره استفاده می‌شود."""

    required_capability = "content.write"
    http_method_names = ["post"]
    max_length = 200_000

    def post(self, request):
        text = request.POST.get("text", "")
        if len(text) > self.max_length:
            return JsonResponse({"error": gettext("The text is too long.")}, status=413)
        return JsonResponse({"html": render_markdown(text)})


# ------------------------------------------------------------------ ترجمه‌ها: وصل کردن و جدا کردن
class TranslationActionView(CapabilityRequiredMixin, View):
    """POST only. ``link``: attach an existing item (by slug) of the same kind as the translation of this one. ``unlink``: take this one out of its group."""

    http_method_names = ["post"]
    model = None
    kind = ""
    action = ""
    required_capability = "content.write"

    def post(self, request, pk):
        from django.contrib import messages
        from django.http import HttpResponseRedirect

        from apps.audit.services import log_event

        obj = get_object_or_404(self.model, pk=pk)
        if not self.can_edit(obj):
            raise PermissionDenied
        back = reverse(f"content:{self.kind}_update", args=[obj.pk])
        if not translations.enabled():
            messages.error(request, gettext("Translations are not available while several sites are enabled."))
            return HttpResponseRedirect(back)
        try:
            if self.action == "unlink":
                translations.unlink(obj)
                message = gettext("“%(title)s” was detached from its translations.") % {"title": obj.title}
            else:
                slug = (request.POST.get("slug") or "").strip().strip("/").rsplit("/", 1)[-1]
                other = self.model.objects.filter(slug=slug).first() if slug else None
                if other is None:
                    raise translations.TranslationError(gettext("No item with this slug was found."))
                if other.pk == obj.pk:
                    raise translations.TranslationError(gettext("An item cannot be its own translation."))
                if not self.can_edit(other):
                    raise PermissionDenied
                translations.link([obj, other])
                message = gettext("“%(other)s” was attached as a translation of “%(title)s”.") % {"other": other.title, "title": obj.title}
        except translations.TranslationError as exc:
            messages.error(request, str(exc))
            return HttpResponseRedirect(back)
        log_event(request, "update", obj, message)
        messages.success(request, message)
        return HttpResponseRedirect(back)

    def can_edit(self, obj) -> bool:
        check = getattr(obj, "user_can_edit", None)
        return check(self.request.user) if check else True


class PostTranslationLinkView(TranslationActionView):
    model, kind, action, required_capability = Post, "post", "link", "content.write"


class PostTranslationUnlinkView(TranslationActionView):
    model, kind, action, required_capability = Post, "post", "unlink", "content.write"


class PageTranslationLinkView(TranslationActionView):
    model, kind, action, required_capability = Page, "page", "link", "pages.manage"


class PageTranslationUnlinkView(TranslationActionView):
    model, kind, action, required_capability = Page, "page", "unlink", "pages.manage"
