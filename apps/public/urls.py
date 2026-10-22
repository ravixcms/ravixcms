from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt

from . import feeds, views

app_name = "public"

# نامک‌ها فقط حروف (فارسی هم)، عدد، «-» و «_» هستند؛ بنابراین مسیرهایی مثل /wp-login.php یا /a.b/ هرگز به یک «صفحه ثابت»
# نمی‌رسند و بدون ریدایرکت اضافه مستقیم ۴۰۴ می‌شوند (جلوگیری از رفتار APPEND_SLASH روی آدرس‌های اسکنر ربات‌ها).
SLUG = r"(?P<slug>[-\w]+)"

# ترتیب مهم است: مسیر «صفحه ثابت» (page_detail) باید آخر باشد؛ نامک‌های رزروشده در apps.core.utils.RESERVED_PAGE_SLUGS.
urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("blog/", views.PostListView.as_view(), name="post_list"),
    re_path(rf"^blog/{SLUG}/$", views.PostDetailView.as_view(), name="post_detail"),
    re_path(rf"^blog/{SLUG}/comment/$", csrf_exempt(views.CommentSubmitView.as_view()), name="comment_submit"),
    re_path(rf"^category/{SLUG}/$", views.CategoryDetailView.as_view(), name="category_detail"),
    re_path(rf"^tag/{SLUG}/$", views.TagDetailView.as_view(), name="tag_detail"),
    # فیدها (RSS در مسیر اصلی، Atom در /atom/): باید پیش از مسیر «صفحه ثابت» بیایند (نامک «feed» رزرو است).
    path("feed/", feeds.site_rss, name="feed"),
    path("feed/atom/", feeds.site_atom, name="feed_atom"),
    re_path(rf"^category/{SLUG}/feed/$", feeds.category_rss, name="category_feed"),
    re_path(rf"^category/{SLUG}/feed/atom/$", feeds.category_atom, name="category_feed_atom"),
    re_path(rf"^tag/{SLUG}/feed/$", feeds.tag_rss, name="tag_feed"),
    re_path(rf"^tag/{SLUG}/feed/atom/$", feeds.tag_atom, name="tag_feed_atom"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("sitemap.xml", views.SitemapView.as_view(), name="sitemap"),
    path("robots.txt", views.RobotsView.as_view(), name="robots"),
    re_path(rf"^{SLUG}/$", views.PageDetailView.as_view(), name="page_detail"),
]
