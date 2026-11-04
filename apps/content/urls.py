from django.urls import path

from . import views

app_name = "content"

urlpatterns = [
    path("posts/", views.PostListView.as_view(), name="post_list"),
    path("posts/new/", views.PostCreateView.as_view(), name="post_create"),
    path("posts/<int:pk>/", views.PostDetailView.as_view(), name="post_detail"),
    path("posts/<int:pk>/edit/", views.PostUpdateView.as_view(), name="post_update"),
    path("posts/<int:pk>/translations/link/", views.PostTranslationLinkView.as_view(), name="post_translation_link"),
    path("posts/<int:pk>/translations/unlink/", views.PostTranslationUnlinkView.as_view(), name="post_translation_unlink"),
    path("posts/<int:pk>/delete/", views.PostDeleteView.as_view(), name="post_delete"),
    path("pages/", views.PageListView.as_view(), name="page_list"),
    path("pages/new/", views.PageCreateView.as_view(), name="page_create"),
    path("pages/<int:pk>/", views.PageDetailView.as_view(), name="page_detail"),
    path("pages/<int:pk>/edit/", views.PageUpdateView.as_view(), name="page_update"),
    path("pages/<int:pk>/translations/link/", views.PageTranslationLinkView.as_view(), name="page_translation_link"),
    path("pages/<int:pk>/translations/unlink/", views.PageTranslationUnlinkView.as_view(), name="page_translation_unlink"),
    path("pages/<int:pk>/delete/", views.PageDeleteView.as_view(), name="page_delete"),
    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_update"),
    path("categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category_delete"),
    path("tags/", views.TagListView.as_view(), name="tag_list"),
    path("tags/new/", views.TagCreateView.as_view(), name="tag_create"),
    path("tags/<int:pk>/edit/", views.TagUpdateView.as_view(), name="tag_update"),
    path("tags/<int:pk>/delete/", views.TagDeleteView.as_view(), name="tag_delete"),
    path("redirects/", views.RedirectListView.as_view(), name="redirect_list"),
    path("redirects/new/", views.RedirectCreateView.as_view(), name="redirect_create"),
    path("redirects/<int:pk>/edit/", views.RedirectUpdateView.as_view(), name="redirect_update"),
    path("redirects/<int:pk>/delete/", views.RedirectDeleteView.as_view(), name="redirect_delete"),
    path("markdown-preview/", views.MarkdownPreviewView.as_view(), name="markdown_preview"),
]
