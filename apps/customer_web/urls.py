from .audience import WebsiteVisitView, WebsiteAnalyticsView, CustomerDirectoryView
from django.urls import path
from .account_views import CustomerAccountView, CustomerAccountReceiptView
from .auth import CustomerAuthView
from .views import ProfileView, FavoriteView, CheckoutMetaView, QuoteView, CheckoutView, OrdersView, CancelView, PaymentProofView, SocketTicketView
from .views import AddressView, AddressDetailView, CartView

urlpatterns = [
    path('traffic/', WebsiteVisitView.as_view()),
    path('analytics/', WebsiteAnalyticsView.as_view()),
    path('directory/', CustomerDirectoryView.as_view()),
    path('directory/<int:contact_id>/', CustomerAccountView.as_view()),
    path('directory/<int:contact_id>/receive/', CustomerAccountView.as_view()),
    path('directory/<int:contact_id>/receipts/<int:receipt_id>/', CustomerAccountReceiptView.as_view()),
    path('auth/<str:action>/', CustomerAuthView.as_view()),
    path('profile/', ProfileView.as_view()),
    path('addresses/', AddressView.as_view()),
    path('addresses/<int:address_id>/', AddressDetailView.as_view()),
    path('cart/', CartView.as_view()),
    path('favorites/<str:product_id>/', FavoriteView.as_view()),
    path('checkout/meta/', CheckoutMetaView.as_view()),
    path('checkout/quote/', QuoteView.as_view()),
    path('checkout/', CheckoutView.as_view()),
    path('orders/', OrdersView.as_view()),
    path('orders/<int:order_id>/cancel/', CancelView.as_view()),
    path('orders/<int:order_id>/receipt/', PaymentProofView.as_view()),
    path('socket-ticket/', SocketTicketView.as_view()),
]
