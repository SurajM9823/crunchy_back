from django.urls import path
from .views import (
    PaymentSettleAPIView,
    InvoiceDetailAPIView,
    PaymentWebhookAPIView,
)

urlpatterns = [
    # Payment Settlement Endpoint (Cash, Card, QR)
    path('settle/', PaymentSettleAPIView.as_view(), name='payment-settle'),

    # Fiscal Tax Invoice Lookup & Thermal Print
    path('invoices/<str:identifier>/', InvoiceDetailAPIView.as_view(), name='invoice-detail'),

    # Digital Wallet Webhooks (eSewa, Khalti, Fonepay)
    path('webhooks/<str:gateway>/', PaymentWebhookAPIView.as_view(), name='payment-webhook'),
]

