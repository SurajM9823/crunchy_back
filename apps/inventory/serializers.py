from rest_framework import serializers
from .models import InventoryCategory, InventoryItem, RecipeItem, StockTransaction


class InventoryCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryCategory
        fields = ['id', 'name', 'description']


class InventoryItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'branch',
            'sku',
            'name',
            'category',
            'category_name',
            'current_stock',
            'unit',
            'min_threshold',
            'cost_per_unit',
            'is_low_stock',
            'is_active',
            'updated_at',
        ]


class InventoryItemCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryItem
        fields = [
            'sku',
            'name',
            'category',
            'current_stock',
            'unit',
            'min_threshold',
            'cost_per_unit',
            'is_active',
        ]


from decimal import Decimal


class InventoryRestockRequestSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))
    cost_per_unit = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


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
