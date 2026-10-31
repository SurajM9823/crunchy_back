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


class DiscountInput(serializers.Serializer):
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)
    discount_remark = serializers.CharField(max_length=180, allow_blank=True, default='')


class CustomerIdentityInput(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=32, allow_blank=True, required=False)
    expected_name = serializers.CharField(max_length=150, allow_blank=True)
    expected_phone = serializers.CharField(max_length=32, allow_blank=True)


class CustomerIdentityView(WebsiteAnalyticsView):
    def patch(self, request, contact_id):
        from .account_services import edit_customer_identity
        branch = audience_branch(request)
        serializer = CustomerIdentityInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        edit_customer_identity(branch, request.user, contact_id, serializer.validated_data)
        return Response(customer_account(branch, contact_id, request.user))


class CustomerAccountView(WebsiteAnalyticsView):
    def patch(self, request, contact_id):
        from .account_services import save_customer_discount
        branch = audience_branch(request)
        serializer = DiscountInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        save_customer_discount(branch, request.user, contact_id, serializer.validated_data)
        return Response(customer_account(branch, contact_id, request.user))

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
