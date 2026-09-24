from rest_framework import serializers
from .models import PaymentTransaction, FiscalInvoice
from .receipt_generator import format_thermal_receipt


class PaymentTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentTransaction
        fields = [
            'id',
            'transaction_id',
            'order',
            'branch',
            'amount',
            'payment_method',
            'status',
            'gateway_ref',
            'idempotency_key',
            'created_at',
        ]


class FiscalInvoiceSerializer(serializers.ModelSerializer):
    thermal_receipt = serializers.SerializerMethodField()
    restaurant_name = serializers.CharField(source='restaurant.name', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    order_number = serializers.CharField(source='order.order_number', read_only=True)

    class Meta:
        model = FiscalInvoice
        fields = [
            'id',
            'invoice_number',
            'order',
            'order_number',
            'restaurant_name',
            'branch_name',
            'seller_pan',
            'customer_name',
            'customer_pan',
            'fiscal_year',
            'subtotal',
            'taxable_amount',
            'vat_amount',
            'cash_round_down_savings',
            'grand_total',
            'payment_method',
            'printed_count',
            'created_at',
            'thermal_receipt',
        ]

    def get_thermal_receipt(self, obj):
        return format_thermal_receipt(obj, width=42)


class PaymentSettleRequestSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(required=True)
    payment_method = serializers.CharField(required=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    customer_pan = serializers.CharField(required=False, allow_blank=True, default="")
    gateway_ref = serializers.CharField(required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(required=False, allow_blank=True, default="")

