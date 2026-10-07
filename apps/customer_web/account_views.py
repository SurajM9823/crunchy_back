from decimal import Decimal
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from apps.daybook.serializers import today
from apps.orders.models import PosReceipt
from apps.orders.receipts import receipt_document
from .audience import WebsiteAnalyticsView, audience_branch
from .account_selectors import customer_account
from .account_services import receive_customer_payment


class CollectionInput(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    method = serializers.ChoiceField(choices=['CASH', 'CARD', 'FONEPAY', 'ESEWA', 'KHALTI', 'BANK_TRANSFER'])
    scope = serializers.ChoiceField(choices=['CREDIT', 'ALL'], default='CREDIT')
    expected_due = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    expected_credit = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    reference = serializers.CharField(max_length=128, allow_blank=True, default='')
    notes = serializers.CharField(max_length=1000, allow_blank=True, default='')
    date = serializers.DateField(default=today)

    def validate_date(self, value):
        if value > today():
            raise serializers.ValidationError('Record money already received, not future payments.')
        return value


class CustomerAccountView(WebsiteAnalyticsView):
    def get(self, request, contact_id):
        return Response(customer_account(audience_branch(request), contact_id, request.user))

    def post(self, request, contact_id):
        branch = audience_branch(request)
        serializer = CollectionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(receive_customer_payment(branch, request.user, contact_id,
            request.headers.get('Idempotency-Key'), serializer.validated_data))


class CustomerAccountReceiptView(WebsiteAnalyticsView):
    def get(self, request, contact_id, receipt_id):
        branch = audience_branch(request)
        row = get_object_or_404(PosReceipt, pk=receipt_id, order__branch=branch, order__customer_contact_id=contact_id)
        return Response(receipt_document(row, request))
