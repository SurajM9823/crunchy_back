from rest_framework import serializers
from decimal import Decimal
from .models import (
    Category,
    Product,
    ProductVariant,
    ModifierGroup,
    ModifierOption,
    OutletProductOverride,
    OutletTimePricingSchedule,
)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'icon_name', 'display_order', 'hsn_code', 'is_archived', 'created_at']


class CategoryCreateSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'icon_name', 'display_order', 'hsn_code']


class ModifierOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModifierOption
        fields = ['id', 'name', 'price_delta', 'is_default']


class ModifierGroupSerializer(serializers.ModelSerializer):
    options = ModifierOptionSerializer(many=True, read_only=True)

    class Meta:
        model = ModifierGroup
        fields = ['id', 'name', 'min_selections', 'max_selections', 'required', 'options']


class ProductVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariant
        fields = ['id', 'name', 'price', 'is_default']


class ProductDetailSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    modifier_groups = ModifierGroupSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id',
            'category',
            'name',
            'description',
            'base_price',
            'cost_price',
            'prep_time_minutes',
            'calories',
            'dietary_tags',
            'images',
            'main_image_index',
            'is_delivery_eligible',
            'is_available',
            'is_web_visible',
            'show_on_pos',
            'show_on_qr',
            'discount_percent',
            'requires_kitchen',
            'is_counter_direct',
            'is_direct_inventory_item',
            'is_combo_package',
            'combo_discount_type',
            'combo_discount_value',
            'combo_original_price',
            'combo_items',
            'variants',
            'modifier_groups',
            'created_at',
            'updated_at',
        ]


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Product
        fields = [
            'id',
            'category',
            'name',
            'description',
            'base_price',
            'cost_price',
            'prep_time_minutes',
            'calories',
            'dietary_tags',
            'images',
            'main_image_index',
            'is_delivery_eligible',
            'is_available',
            'is_web_visible',
            'show_on_pos',
            'show_on_qr',
            'discount_percent',
            'requires_kitchen',
            'is_counter_direct',
            'is_direct_inventory_item',
            'is_combo_package',
            'combo_discount_type',
            'combo_discount_value',
            'combo_original_price',
            'combo_items',
        ]


class OutletProductOverrideSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    base_price = serializers.DecimalField(source='product.base_price', max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = OutletProductOverride
        fields = [
            'id',
            'branch',
            'product',
            'product_name',
            'base_price',
            'is_available',
            'price_override',
            'is_web_visible',
            'show_on_pos',
            'show_on_qr',
            'updated_at',
        ]


class OutletStockToggleSerializer(serializers.Serializer):
    is_available = serializers.BooleanField(required=True)
    price_override = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)

