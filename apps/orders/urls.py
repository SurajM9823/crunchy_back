from django.urls import path
from .receipts import CustomerOrderSlipView, SelfServiceReceiptView, ReceiptTrackingView
from .self_service import SelfServiceCheckoutView, SelfServiceOrderView, SelfServiceTablesView, SelfServiceQuoteView, SelfServiceVoidView
from .pos_views import PosOrdersView, PosQuoteView, PosMetaView, PosDetailView, PosCommandView, PosReceiptView, PosSocketTicketView, PosBillQuoteView, PosLayoutView
from .views import (
    CheckoutAPIView,
    OrderDetailAPIView,
    OutletOrderListAPIView,
    KitchenTicketsAPIView,
    OrderStatusTransitionAPIView,
    LiveDisplayAPIView,
)

urlpatterns = [
    path('self-service/items/void/', SelfServiceVoidView.as_view()),
    path('tracking/', ReceiptTrackingView.as_view()),
    path('customer/<int:order_id>/slip/', CustomerOrderSlipView.as_view()),
    path('self-service/receipt/', SelfServiceReceiptView.as_view()),
    path('self-service/quote/', SelfServiceQuoteView.as_view()),
    path('self-service/checkout/', SelfServiceCheckoutView.as_view()),
    path('self-service/order/', SelfServiceOrderView.as_view()),
    path('self-service/tables/<int:outlet_id>/', SelfServiceTablesView.as_view()),
    path('pos/', PosOrdersView.as_view()),
    path('pos/quote/', PosQuoteView.as_view()),
    path('pos/meta/', PosMetaView.as_view()),
    path('pos/table-groups/', PosLayoutView.as_view(), {'kind': 'group'}),
    path('pos/table-groups/<int:object_id>/', PosLayoutView.as_view(), {'kind': 'group'}),
    path('pos/tables/', PosLayoutView.as_view(), {'kind': 'table'}),
    path('pos/tables/<int:object_id>/', PosLayoutView.as_view(), {'kind': 'table'}),
    path('pos/socket-ticket/', PosSocketTicketView.as_view()),
    path('pos/receipts/<int:receipt_id>/', PosReceiptView.as_view()),
    path('pos/<int:order_id>/', PosDetailView.as_view()),
    path('pos/<int:order_id>/billing-quote/', PosBillQuoteView.as_view()),
    *[path(f'pos/<int:order_id>/{action}/', PosCommandView.as_view(), {'action':action}) for action in ['append','settle','transition','void','refund','call','bill','round']],
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

