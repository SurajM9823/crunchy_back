from decimal import Decimal
from rest_framework import serializers
from .models import (
    InventoryCategory,
    InventoryItem,
    RecipeItem,
    StockTransaction,
    Supplier,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    StockMovementLedger,
    PaymentStatus,
    PaymentMethod,
)


class InventoryCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryCategory
        fields = ['id', 'name', 'description']


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            'id',
            'branch',
            'name',
            'pan_number',
            'phone',
            'email',
            'address',
            'credit_balance',
            'is_active',
            'created_at',
            'updated_at',
        ]


class SupplierCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            'name',
            'pan_number',
            'phone',
            'email',
            'address',
            'credit_balance',
            'is_active',
        ]


class InventoryItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)
    total_valuation = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'branch',
            'sku',
            'name',
            'category',
            'category_name',
            'supplier',
            'supplier_name',
            'current_stock',
            'unit',
            'min_threshold',
            'cost_per_unit',
            'total_valuation',
            'is_low_stock',
            'is_active',
            'last_restocked',
            'updated_at',
        ]


class InventoryItemCreateUpdateSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = InventoryItem
        fields = [
            'sku',
            'name',
            'category',
            'supplier',
            'supplier_name',
            'current_stock',
            'unit',
            'min_threshold',
            'cost_per_unit',
            'is_active',
        ]


class InventoryRestockRequestSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))
    cost_per_unit = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PurchaseInvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseInvoiceItem
        fields = [
            'id',
            'item',
            'item_name',
            'category',
            'quantity',
            'unit',
            'unit_cost',
            'discount',
            'total_cost',
            'batch_no',
            'expiry_date',
        ]


class PurchaseInvoiceItemCreateSerializer(serializers.Serializer):
    item_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    item_name = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    sku = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))
    unit = serializers.CharField(required=False, default="pcs")
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.00'))
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    total_cost = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    batch_no = serializers.CharField(required=False, allow_blank=True, default="")
    expiry_date = serializers.DateField(required=False, allow_null=True)


class PurchaseInvoiceSerializer(serializers.ModelSerializer):
    items = PurchaseInvoiceItemSerializer(many=True, read_only=True)
    received_by_name = serializers.CharField(source='received_by.username', read_only=True)
    branch_name = serializers.CharField(source='branch.name', read_only=True)

    class Meta:
        model = PurchaseInvoice
        fields = [
            'id',
            'branch',
            'branch_name',
            'invoice_number',
            'supplier',
            'supplier_name',
            'supplier_phone',
            'purchase_date',
            'subtotal',
            'discount_amount',
            'total_amount',
            'paid_amount',
            'due_amount',
            'payment_status',
            'payment_method',
            'notes',
            'document',
            'document_name',
            'received_by',
            'received_by_name',
            'idempotency_key',
            'items',
            'created_at',
            'updated_at',
        ]


class PurchaseInvoiceCreateSerializer(serializers.Serializer):
    invoice_number = serializers.CharField(max_length=100)
    supplier_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    supplier_id = serializers.CharField(max_length=64, required=False, allow_null=True, allow_blank=True)
    supplier_phone = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    purchase_date = serializers.DateField(required=False, allow_null=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    due_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    payment_status = serializers.ChoiceField(choices=PaymentStatus.choices, required=False, default=PaymentStatus.PAID)
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, required=False, default=PaymentMethod.CASH)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    document = serializers.FileField(required=False, allow_null=True)
    document_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    idempotency_key = serializers.CharField(max_length=128, required=False, allow_blank=True, allow_null=True)
    outlet_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    items = PurchaseInvoiceItemCreateSerializer(many=True)


class StockMovementLedgerSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.CharField(source='recorded_by.username', read_only=True)
    item_sku = serializers.CharField(source='item.sku', read_only=True)

    class Meta:
        model = StockMovementLedger
        fields = [
            'id',
            'branch',
            'item',
            'item_sku',
            'item_name',
            'category',
            'type',
            'quantity',
            'unit',
            'previous_stock',
            'new_stock',
            'reason',
            'reference_id',
            'note',
            'recorded_by',
            'recorded_by_name',
            'timestamp',
        ]


class InventoryAuditReconcileItemSerializer(serializers.Serializer):
    item_id = serializers.IntegerField()
    physical_stock = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.000'))
    note = serializers.CharField(required=False, allow_blank=True, default="")


class InventoryAuditReconcileRequestSerializer(serializers.Serializer):
    outlet_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    items = InventoryAuditReconcileItemSerializer(many=True)


class RecipeItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    variant_name = serializers.CharField(source='variant.name', read_only=True)
    ingredient_name = serializers.CharField(source='inventory_item.name', read_only=True)
    ingredient_unit = serializers.CharField(source='inventory_item.unit', read_only=True)

    class Meta:
        model = RecipeItem
        fields = [
            'id',
            'product',
            'product_name',
            'variant',
            'variant_name',
            'inventory_item',
            'ingredient_name',
            'ingredient_unit',
            'quantity_required',
        ]


class RecipeItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeItem
        fields = ['product', 'variant', 'inventory_item', 'quantity_required']


class StockTransactionSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='inventory_item.name', read_only=True)
    sku = serializers.CharField(source='inventory_item.sku', read_only=True)
    performed_by_name = serializers.CharField(source='performed_by.username', read_only=True)
    order_number = serializers.CharField(source='reference_order.order_number', read_only=True)

    class Meta:
        model = StockTransaction
        fields = [
            'id',
            'sku',
            'item_name',
            'transaction_type',
            'quantity',
            'previous_stock',
            'resulting_stock',
            'order_number',
            'performed_by_name',
            'notes',
            'created_at',
        ]
