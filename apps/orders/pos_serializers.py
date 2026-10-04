from decimal import Decimal
from rest_framework import serializers
from apps.catalog.serializers import QuoteLineSerializer
from .models import OrderStatus, FulfillmentType

METHODS = ['CASH','CARD','FONEPAY','ESEWA','KHALTI','BANK_TRANSFER','CREDIT']

class PosLineSerializer(QuoteLineSerializer):
    item_notes = serializers.CharField(max_length=255, allow_blank=True, default='')

class TenderSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=METHODS)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    reference = serializers.CharField(max_length=128, allow_blank=True, default='')

class PosQuoteSerializer(serializers.Serializer):
    customer_phone = serializers.CharField(max_length=32, allow_blank=True, default='')
    order_id = serializers.IntegerField(min_value=1, required=False)
    items = PosLineSerializer(many=True, allow_empty=False, max_length=100)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, default=0)
    payment_method = serializers.ChoiceField(choices=METHODS + ['SPLIT'], default='CASH')

class PosCreateSerializer(PosQuoteSerializer):
    customer_name = serializers.CharField(max_length=120, allow_blank=True, default='Walk-in Guest')
    customer_phone = serializers.CharField(max_length=32, allow_blank=True, default='')
    fulfillment_type = serializers.ChoiceField(choices=FulfillmentType.choices, default='TAKEAWAY')
    table_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    delivery_address = serializers.CharField(max_length=1000, allow_blank=True, default='')
    notes = serializers.CharField(max_length=2000, allow_blank=True, default='')
    discount_reason = serializers.CharField(max_length=255, allow_blank=True, default='')
    tenders = TenderSerializer(many=True, max_length=10, default=list)
    expected_total = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

class VersionSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class PosBillQuoteSerializer(VersionSerializer):
    customer_phone = serializers.CharField(max_length=32, allow_blank=True, required=False)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)

class PosAppendSerializer(VersionSerializer):
    items = PosLineSerializer(many=True, allow_empty=False, max_length=100)
    expected_total = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

class PosSettleSerializer(VersionSerializer):
    tenders = TenderSerializer(many=True, allow_empty=False, max_length=10)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0, required=False)
    discount_reason = serializers.CharField(max_length=255, allow_blank=True, default='')
    customer_name = serializers.CharField(max_length=120, allow_blank=True, required=False)
    customer_phone = serializers.CharField(max_length=32, allow_blank=True, required=False)

class PosTransitionSerializer(VersionSerializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    reason = serializers.CharField(max_length=255, allow_blank=True, default='')

class PosVoidSerializer(VersionSerializer):
    item_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, required=False)
    reason = serializers.CharField(max_length=255, allow_blank=False)


class PosRoundSerializer(VersionSerializer):
    round_number = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=['PREPARING','READY','SERVED'])


class PosCallSerializer(VersionSerializer):
    round_number = serializers.IntegerField(min_value=1, required=False)

class PosRefundSerializer(VersionSerializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    method = serializers.ChoiceField(choices=[m for m in METHODS if m != 'CREDIT'])
    reference = serializers.CharField(max_length=128, allow_blank=True, default='')
    reason = serializers.CharField(max_length=255, allow_blank=False)

class PosListSerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    status = serializers.ChoiceField(choices=['ALL'] + list(OrderStatus.values), default='ALL')
    fulfillment = serializers.ChoiceField(choices=['ALL'] + list(FulfillmentType.values), default='ALL')
    settlement = serializers.ChoiceField(choices=['ALL','PAID','UNPAID','PARTIAL','CREDIT','REFUNDED'], default='ALL')
    search = serializers.CharField(max_length=100, allow_blank=True, default='')
    page = serializers.IntegerField(min_value=1, max_value=100000, default=1)
    page_size = serializers.ChoiceField(choices=[10,25,50,100], default=25)
    active = serializers.BooleanField(default=False)
    open_tabs = serializers.BooleanField(default=False)
    kitchen = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs.get('start_date') and attrs.get('end_date') and attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError('End date must be on or after start date.')
        return attrs
