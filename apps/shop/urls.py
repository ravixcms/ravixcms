from django.urls import path

from . import views

app_name = "shop"

urlpatterns = [
    path("shop/", views.ProductListView.as_view(), name="list"),
    path("shop/p/<str:slug>/", views.ProductDetailView.as_view(), name="product"),
    path("shop/cart/", views.CartView.as_view(), name="cart"),
    path("shop/cart/add/", views.CartAddView.as_view(), name="cart_add"),
    path("shop/cart/update/", views.CartUpdateView.as_view(), name="cart_update"),
    path("shop/checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("shop/my-orders/", views.MyOrdersView.as_view(), name="my_orders"),
    path("shop/order/<str:number>/<str:token>/download/<int:item_id>/", views.DownloadView.as_view(), name="download"),
    path("shop/order/<str:number>/<str:token>/", views.OrderView.as_view(), name="order"),
    path("shop/order/<str:number>/<str:token>/pay/", views.OrderPayView.as_view(), name="order_pay"),
    path("shop/pay/callback/<str:reference>/", views.PaymentCallbackView.as_view(), name="callback"),
    path("shop/pay/sandbox/<str:reference>/", views.SandboxPayView.as_view(), name="sandbox_pay"),
]
