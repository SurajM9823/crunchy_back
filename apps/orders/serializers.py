from rest_framework import serializers
from .models import Order, OrderItem, OrderItemModifier, OrderStatusHistory


class OrderItemModifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItemModifier
        fields = ['id', 'group_name', 'option_name', 'price_delta']


class OrderItemSerializer(serializers.ModelSerializer):
    modifiers = OrderItemModifierSerializer(many=True, read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            'id',
            'product',
            'product_name',
            'variant',
            'variant_name',
            'unit_price',
            'quantity',
            'line_total',
            'requires_kitchen',
            'round_number',
            'item_notes',
            'modifiers',
        ]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source='changed_by.username', read_only=True)

    class Meta:
        model = OrderStatusHistory
        fields = ['id', 'from_status', 'to_status', 'changed_by_name', 'notes', 'created_at']


class OrderDetailSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    branch_code = serializers.CharField(source='branch.branch_code', read_only=True)
    table_number = serializers.CharField(source='table.table_number', read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id',
            'order_number',
            'branch',
            'branch_name',
            'branch_code',
            'table',
            'table_number',
            'table_session_id',
            'customer_name',
            'customer_phone',
            'fulfillment_type',
            'order_source',
            'status',
            'payment_method',
            'payment_status',
            'subtotal',
            'vat_included_amount',
            'discount_amount',
            'cash_round_down_savings',
            'total_payable',
            'delivery_address',
            'notes',
            'items',
            'status_history',
            'created_at',
            'updated_at',
        ]


class CheckoutItemRequestSerializer(serializers.Serializer):
    product_id = serializers.CharField(required=True)
    variant_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    modifier_option_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    quantity = serializers.IntegerField(default=1, min_value=1)
    item_notes = serializers.CharField(required=False, allow_blank=True, default="")


class CheckoutRequestSerializer(serializers.Serializer):
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    qr_token = serializers.CharField(required=False, allow_blank=True)
    table_id = serializers.IntegerField(required=False, allow_null=True)
    fulfillment_type = serializers.CharField(default="DINE_IN")
    order_source = serializers.CharField(default="TABLE_QR")
    customer_name = serializers.CharField(default="Guest")
    customer_phone = serializers.CharField(required=False, allow_blank=True, default="")
    payment_method = serializers.CharField(default="CASH")
    delivery_address = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    quote_timestamp = serializers.IntegerField(required=False, allow_null=True)
    items = CheckoutItemRequestSerializer(many=True, required=True)


class OrderStatusTransitionSerializer(serializers.Serializer):
    to_status = serializers.CharField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

