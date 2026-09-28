from decimal import Decimal
from rest_framework import serializers
from .models import (Category, Product, ProductVariant, ModifierGroup, ModifierOption,
                     OutletProductOverride, OutletTimePricingSchedule)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'icon_name', 'display_order', 'hsn_code', 'is_archived']


class CategoryCreateSerializer(CategorySerializer):
    id = serializers.CharField(required=False, max_length=64)


class ModifierOptionSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False, max_length=64)
    price_delta = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=0, default=Decimal('0'))
    class Meta:
        model = ModifierOption
        fields = ['id', 'name', 'price_delta', 'is_default']


class ModifierGroupSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False, max_length=64)
    options = ModifierOptionSerializer(many=True, max_length=50)
    class Meta:
        model = ModifierGroup
        fields = ['id', 'name', 'min_selections', 'max_selections', 'required', 'options']

    def validate(self, data):
        minimum = max(data.get('min_selections', 0), int(data.get('required', False)))
        maximum = data.get('max_selections', 1)
        if not 0 <= minimum <= maximum <= len(data['options']):
            raise serializers.ValidationError('Selection limits must fit the available options.')
        defaults = sum(bool(o.get('is_default')) for o in data['options'])
        if defaults > maximum:
            raise serializers.ValidationError('Too many default options.')
        data['min_selections'] = minimum
        return data


class ProductVariantSerializer(serializers.ModelSerializer):
    id = serializers.CharField(required=False, max_length=64)
    class Meta:
        model = ProductVariant
        fields = ['id', 'name', 'price', 'is_default']


class ComboItemSerializer(serializers.Serializer):
    product_id = serializers.CharField(max_length=64)
    quantity = serializers.IntegerField(min_value=1, max_value=100)


class RecipeInputSerializer(serializers.Serializer):
    inventory_item_id = serializers.IntegerField(min_value=1)
    quantity_required = serializers.DecimalField(max_digits=10, decimal_places=3, min_value=Decimal('0.001'))


PRODUCT_FIELDS = ['id', 'category', 'name', 'description', 'base_price', 'cost_price',
    'prep_time_minutes', 'calories', 'dietary_tags', 'images', 'main_image_index',
    'is_delivery_eligible', 'is_available', 'is_web_visible', 'show_on_pos', 'show_on_qr',
    'discount_percent', 'requires_kitchen', 'is_counter_direct', 'is_direct_inventory_item',
    'linked_inventory_item', 'is_combo_package', 'combo_discount_type', 'combo_discount_value',
    'combo_original_price', 'combo_items', 'variants', 'modifier_groups', 'recipe_ingredients']


class ProductDetailSerializer(serializers.ModelSerializer):
    linked_inventory_item = serializers.SerializerMethodField()
    variants = ProductVariantSerializer(many=True, read_only=True)
    modifier_groups = ModifierGroupSerializer(many=True, read_only=True)
    recipe_ingredients = serializers.SerializerMethodField()
    class Meta:
        model = Product
        fields = PRODUCT_FIELDS

    def get_recipe_ingredients(self, obj):
        branch = self.context.get('branch')
        return [{'id': row.pk, 'inventory_item_id': row.inventory_item_id,
                 'inventory_item_name': row.inventory_item.name, 'unit': row.inventory_item.unit,
                 'quantity_required': str(row.quantity_required)}
                for row in obj.recipe_items.all()
                if branch and row.inventory_item.branch_id == branch.pk and row.variant_id is None]

    def get_linked_inventory_item(self, obj):
        recipes = self.get_recipe_ingredients(obj)
        return recipes[0]['inventory_item_id'] if obj.is_direct_inventory_item and len(recipes) == 1 else None


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    linked_inventory_item = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    id = serializers.CharField(required=False, max_length=64)
    variants = ProductVariantSerializer(many=True, required=False, max_length=50)
    modifier_groups = ModifierGroupSerializer(many=True, required=False, max_length=30)
    combo_items = ComboItemSerializer(many=True, required=False, max_length=50)
    recipe_ingredients = RecipeInputSerializer(many=True, required=False, max_length=100)
    images = serializers.ListField(child=serializers.URLField(max_length=2048), max_length=10, required=False)
    dietary_tags = serializers.ListField(child=serializers.ChoiceField(choices=["Halal", "Spicy", "Vegetarian", "Chef's Choice", "Popular"]), max_length=5, required=False)
    class Meta:
        model = Product
        fields = PRODUCT_FIELDS
        read_only_fields = ['combo_original_price']
        extra_kwargs = {'cost_price': {'min_value': 0}, 'combo_discount_value': {'min_value': 0}}

    def validate(self, data):
        branch = self.context['branch']
        category = data.get('category', getattr(self.instance, 'category', None))
        if category and category.restaurant_id != branch.restaurant_id:
            raise serializers.ValidationError({'category': 'Unknown category for this restaurant.'})
        if self.instance and 'id' in data and data['id'] != self.instance.pk:
            raise serializers.ValidationError({'id': 'Product IDs cannot be changed.'})
        if not self.instance and data.get('id') and Product.objects.filter(pk=data['id']).exists():
            raise serializers.ValidationError({'id': 'This product ID already exists.'})
        images = data.get('images', getattr(self.instance, 'images', []))
        index = data.get('main_image_index', getattr(self.instance, 'main_image_index', 0))
        if index and index >= len(images):
            raise serializers.ValidationError({'main_image_index': 'Choose an existing image.'})
        variants = data.get('variants', [])
        if variants and sum(bool(v.get('is_default')) for v in variants) != 1:
            raise serializers.ValidationError({'variants': 'Select exactly one default variant.'})
        return data


class OutletProductOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutletProductOverride
        fields = ['id', 'branch', 'product', 'is_available', 'price_override', 'is_web_visible', 'show_on_pos', 'show_on_qr']


class OutletStockToggleSerializer(serializers.Serializer):
    is_available = serializers.BooleanField(required=False)
    price_override = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, allow_null=True)
    is_web_visible = serializers.BooleanField(required=False, allow_null=True)
    show_on_pos = serializers.BooleanField(required=False, allow_null=True)
    show_on_qr = serializers.BooleanField(required=False, allow_null=True)


class ScheduleSerializer(serializers.ModelSerializer):
    days = serializers.ListField(child=serializers.ChoiceField(choices=['Mon','Tue','Wed','Thu','Fri','Sat','Sun']), allow_empty=False, max_length=7)
    channels = serializers.ListField(child=serializers.ChoiceField(choices=['web','qr','pos','kiosk','delivery']), allow_empty=False, max_length=5)
    product_ids = serializers.ListField(child=serializers.CharField(max_length=64), max_length=10000, required=False)
    class Meta:
        model = OutletTimePricingSchedule
        fields = ['id', 'name', 'start_time', 'end_time', 'days', 'channels', 'adjustment_percentage',
                  'discount_percentage', 'product_ids', 'is_active', 'priority']
        extra_kwargs = {'discount_percentage': {'required': False}}

    def validate(self, data):
        start = data.get('start_time', getattr(self.instance, 'start_time', None))
        end = data.get('end_time', getattr(self.instance, 'end_time', None))
        if start == end:
            raise serializers.ValidationError('Start and end times must differ.')
        if start.second or end.second or start.microsecond or end.microsecond:
            raise serializers.ValidationError('Use minute precision for pricing windows.')
        if data.get('adjustment_percentage') is not None:
            data['discount_percentage'] = max(Decimal('0'), -data['adjustment_percentage'])
        elif 'discount_percentage' in data:
            data['adjustment_percentage'] = -data['discount_percentage']
        elif not self.instance and 'discount_percentage' not in data:
            raise serializers.ValidationError('Provide an adjustment percentage.')
        return data


class ComboSelectionSerializer(serializers.Serializer):
    product_id = serializers.CharField(max_length=64)
    variant_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    modifier_option_ids = serializers.ListField(child=serializers.CharField(max_length=64), max_length=100, default=list)
    quantity = serializers.IntegerField(min_value=1, max_value=100, default=1)


class QuoteLineSerializer(ComboSelectionSerializer):
    combo_selections = ComboSelectionSerializer(many=True, required=False, allow_empty=False, max_length=100)


class QuoteSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=['web','qr','pos','kiosk','delivery'], default='web')
    items = QuoteLineSerializer(many=True, allow_empty=False, max_length=100)


class PricingSummarySerializer(serializers.Serializer):
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    payment_method = serializers.ChoiceField(choices=['CASH','CARD','ESEWA','KHALTI','FONEPAY','PAY_AT_COUNTER'], default='CASH')
