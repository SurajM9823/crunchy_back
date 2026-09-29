import math
from rest_framework import serializers
from apps.orders.pos_serializers import PosLineSerializer
from .models import CustomerAddress


class AddressSerializer(serializers.ModelSerializer):
    latitude = serializers.DecimalField(max_digits=10, decimal_places=7, min_value=-90, max_value=90, allow_null=True, required=False)
    longitude = serializers.DecimalField(max_digits=10, decimal_places=7, min_value=-180, max_value=180, allow_null=True, required=False)

    class Meta:
        model = CustomerAddress
        fields = ['id', 'label', 'address', 'landmark', 'latitude', 'longitude', 'is_default']
        read_only_fields = ['id']

    def validate(self, data):
        lat = data.get('latitude', getattr(self.instance, 'latitude', None))
        lng = data.get('longitude', getattr(self.instance, 'longitude', None))
        if (lat is None) != (lng is None):
            raise serializers.ValidationError('Provide both map coordinates.')
        return data


class CartVariantSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=100, allow_blank=True)
    name = serializers.CharField(max_length=200, allow_blank=True)
    price = serializers.FloatField(min_value=0, max_value=99999999)
    isDefault = serializers.BooleanField(required=False)


class CartModifierSerializer(serializers.Serializer):
    groupId = serializers.CharField(max_length=100)
    groupName = serializers.CharField(max_length=200, allow_blank=True)
    optionId = serializers.CharField(max_length=100)
    optionName = serializers.CharField(max_length=500, allow_blank=True)
    priceDelta = serializers.FloatField(min_value=0, max_value=99999999)


class CartItemSerializer(serializers.Serializer):
    # Display snapshots are not prices accepted at checkout; the quote engine reprices IDs.
    cartItemId = serializers.CharField(max_length=120)
    productId = serializers.CharField(max_length=64)
    productName = serializers.CharField(max_length=200)
    image = serializers.CharField(max_length=2000, allow_blank=True, default='')
    variant = CartVariantSerializer()
    selectedModifiers = CartModifierSerializer(many=True, max_length=100)
    quantity = serializers.IntegerField(min_value=1, max_value=100)
    unitPrice = serializers.FloatField(min_value=0, max_value=99999999)
    lineTotal = serializers.FloatField(min_value=0, max_value=9999999999)
    addedAt = serializers.FloatField(min_value=0)
    quoteExpiresAt = serializers.FloatField(min_value=0)
    comboSelections = PosLineSerializer(many=True, max_length=100, required=False)


class CartInput(serializers.Serializer):
    items = CartItemSerializer(many=True, max_length=100)
    version = serializers.IntegerField(min_value=0, required=False)
    merge_id = serializers.UUIDField(required=False)

    def validate_items(self, items):
        if len({row['cartItemId'] for row in items}) != len(items):
            raise serializers.ValidationError('Cart line IDs must be unique.')
        def finite(value):
            if isinstance(value, float) and not math.isfinite(value):
                raise serializers.ValidationError('Cart numbers must be finite.')
            if isinstance(value, dict):
                for child in value.values():
                    finite(child)
            if isinstance(value, list):
                for child in value:
                    finite(child)
        finite(items)
        return items
