from django.urls import path
from .views import (
    CheckoutAPIView,
    OrderDetailAPIView,
    OutletOrderListAPIView,
    KitchenTicketsAPIView,
    OrderStatusTransitionAPIView,
    LiveDisplayAPIView,
)

urlpatterns = [
    # Universal Checkout API (Table QR, POS, Kiosk, Customer App)
    path('checkout/', CheckoutAPIView.as_view(), name='order-checkout'),

    # Outlet Admin & POS Orders List
    path('', OutletOrderListAPIView.as_view(), name='order-list'),
    path('outlet/me/', OutletOrderListAPIView.as_view(), name='order-outlet-me'),

    # KDS Kitchen Preparation Tickets
    path('kitchen/me/', KitchenTicketsAPIView.as_view(), name='order-kitchen-me'),

    # Order Detail
    path('<str:identifier>/', OrderDetailAPIView.as_view(), name='order-detail'),

    # Status Transition (Zero Page Reload)
    path('<int:order_id>/transition/', OrderStatusTransitionAPIView.as_view(), name='order-transition'),

    # Live TV Pickup Display
    path('display/<int:outlet_id>/', LiveDisplayAPIView.as_view(), name='order-display'),
]

