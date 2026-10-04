from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from rest_framework import serializers

METHODS = ['CASH', 'CARD', 'FONEPAY', 'ESEWA', 'KHALTI', 'BANK_TRANSFER', 'CHEQUE', 'OTHER']


def today():
    return datetime.now(ZoneInfo('Asia/Kathmandu')).date()


class EntryInput(serializers.Serializer):
    date = serializers.DateField(default=today)
    direction = serializers.ChoiceField(choices=['IN', 'OUT'])
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    payment_method = serializers.ChoiceField(choices=METHODS, default='CASH')
    category = serializers.CharField(max_length=80)
    party = serializers.CharField(max_length=150, allow_blank=True, default='')
    description = serializers.CharField(max_length=1000)
    reference = serializers.CharField(max_length=128, allow_blank=True, default='')

    def validate_date(self, value):
        if value > today():
            raise serializers.ValidationError('Entries must represent money already received or paid out.')
        return value


class LedgerQuery(serializers.Serializer):
    date = serializers.DateField(default=today)
    direction = serializers.ChoiceField(choices=['ALL', 'IN', 'OUT'], default='ALL')
    payment_method = serializers.ChoiceField(choices=['ALL']+METHODS, default='ALL')
    search = serializers.CharField(max_length=100, allow_blank=True, default='')
    include_voided = serializers.BooleanField(default=False)
    page = serializers.IntegerField(min_value=1, max_value=100000, default=1)
    page_size = serializers.ChoiceField(choices=[10, 25, 50, 100], default=25)


class ImportQuery(serializers.Serializer):
    date = serializers.DateField(default=today)
    kind = serializers.ChoiceField(choices=['SALE', 'REFUND'], default='SALE')


class ImportInput(ImportQuery):
    def validate_date(self, value):
        return EntryInput().validate_date(value)

    payment_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), allow_empty=False, max_length=5000)

    def validate_payment_ids(self, value):
        return sorted(set(value))


class VoidInput(serializers.Serializer):
    reason = serializers.CharField(max_length=500)
