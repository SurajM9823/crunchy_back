from django.urls import path
from .auth import CustomerAuthView
from .views import ProfileView, FavoriteView, CheckoutMetaView, QuoteView, CheckoutView, OrdersView, CancelView, PaymentProofView, SocketTicketView
from .views import AddressView, AddressDetailView, CartView

urlpatterns = [
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
