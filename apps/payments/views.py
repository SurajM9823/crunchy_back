from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.core.exceptions import ValidationError

from apps.orders.models import Order
from apps.orders.selectors import get_order_by_id, get_order_by_number
from .models import FiscalInvoice
from .services import order_settle_payment
from .serializers import (
    PaymentSettleRequestSerializer,
    FiscalInvoiceSerializer,
    PaymentTransactionSerializer,
)


class PaymentSettleAPIView(APIView):
    """
    POST /api/v1/payments/settle/
    Settles an order, closes table session, generates Fiscal Tax Receipt,
    and broadcasts live WebSocket notification with Zero Page Reload!
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PaymentSettleRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        order = get_order_by_id(data['order_id'])
        if not order:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        received_by = request.user if request.user.is_authenticated else None

        try:
            txn, invoice = order_settle_payment(
                order=order,
                payment_method=data['payment_method'],
                amount=data.get('amount'),
                idempotency_key=data.get('idempotency_key'),
                received_by=received_by,
                customer_pan=data.get('customer_pan', ''),
                gateway_ref=data.get('gateway_ref', ''),
            )

            return Response({
                "message": "Order payment settled successfully.",
                "transaction": PaymentTransactionSerializer(txn).data,
                "invoice": FiscalInvoiceSerializer(invoice).data if invoice else None,
            }, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class InvoiceDetailAPIView(APIView):
    """
    GET /api/v1/payments/invoices/<identifier>/
    Lookup official fiscal invoice by invoice_number or order_number.
    Returns full invoice metadata and formatted 80mm thermal receipt text.
    """
    permission_classes = [AllowAny]

    def get(self, request, identifier):
        invoice = (
            FiscalInvoice.objects
            .select_related('order', 'branch', 'restaurant', 'order__table')
            .prefetch_related('order__items', 'order__items__modifiers')
            .filter(invoice_number=identifier)
            .first()
        )
        if not invoice:
            # Try by order_number
            invoice = (
                FiscalInvoice.objects
                .select_related('order', 'branch', 'restaurant', 'order__table')
                .prefetch_related('order__items', 'order__items__modifiers')
                .filter(order__order_number=identifier)
                .first()
            )

        if not invoice:
            return Response({"detail": "Fiscal invoice not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(FiscalInvoiceSerializer(invoice).data, status=status.HTTP_200_OK)


class PaymentWebhookAPIView(APIView):
    """
    POST /api/v1/payments/webhooks/<gateway>/
    Idempotent gateway callback receiver for digital wallets (eSewa, Khalti, Fonepay).
    """
    permission_classes = [AllowAny]

    def post(self, request, gateway):
        payload = request.data
        order_identifier = payload.get('order_id') or payload.get('order_number') or payload.get('purchase_order_id')
        gateway_ref = payload.get('ref_id') or payload.get('idx') or payload.get('trace_id') or ""

        if not order_identifier:
            return Response({"detail": "Missing order reference."}, status=status.HTTP_400_BAD_REQUEST)

        order = None
        if str(order_identifier).isdigit():
            order = get_order_by_id(int(order_identifier))
        if not order:
            order = get_order_by_number(str(order_identifier))

        if not order:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        idempotency_key = f"{gateway}:{gateway_ref}" if gateway_ref else None

        txn, invoice = order_settle_payment(
            order=order,
            payment_method=gateway.upper(),
            idempotency_key=idempotency_key,
            gateway_ref=gateway_ref,
            raw_response=payload,
        )

        return Response({
            "status": "success",
            "transaction_id": txn.transaction_id,
            "invoice_number": invoice.invoice_number if invoice else None,
        }, status=status.HTTP_200_OK)

