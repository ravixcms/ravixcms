from django.urls import path

from . import panel

app_name = "shop_panel"

urlpatterns = [
    path("shop/products/", panel.ProductListView.as_view(), name="products"),
    path("shop/products/new/", panel.ProductEditView.as_view(), name="product_create"),
    path("shop/products/<int:pk>/", panel.ProductEditView.as_view(), name="product_edit"),
    path("shop/products/<int:pk>/delete/", panel.ProductDeleteView.as_view(), name="product_delete"),
    path("shop/products/<int:pk>/gallery/", panel.GalleryAddView.as_view(), name="gallery_add"),
    path("shop/products/<int:pk>/gallery/<int:image_id>/delete/", panel.GalleryDeleteView.as_view(), name="gallery_delete"),
    path("shop/categories/", panel.CategoryListView.as_view(), name="categories"),
    path("shop/categories/new/", panel.CategoryEditView.as_view(), name="category_create"),
    path("shop/categories/<int:pk>/", panel.CategoryEditView.as_view(), name="category_edit"),
    path("shop/categories/<int:pk>/delete/", panel.CategoryDeleteView.as_view(), name="category_delete"),
    path("shop/gateways/", panel.GatewayListView.as_view(), name="gateways"),
    path("shop/gateways/<str:name>/", panel.GatewayEditView.as_view(), name="gateway_edit"),
    path("shop/gateways/<str:name>/toggle/", panel.GatewayToggleView.as_view(), name="gateway_toggle"),
    path("shop/orders/<int:pk>/payments/<int:payment_id>/inquire/", panel.PaymentInquireView.as_view(), name="payment_inquire"),
    path("shop/orders/", panel.OrderListView.as_view(), name="orders"),
    path("shop/orders/<int:pk>/", panel.OrderDetailView.as_view(), name="order"),
]
