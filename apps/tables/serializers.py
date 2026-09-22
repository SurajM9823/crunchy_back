from rest_framework import serializers
from .models import DiningTable
from .qr_security import generate_table_qr_token


class DiningTableSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source='branch.name', read_only=True)
    branch_code = serializers.CharField(source='branch.branch_code', read_only=True)
    qr_token = serializers.SerializerMethodField()

    class Meta:
        model = DiningTable
        fields = [
            'id',
            'branch',
            'branch_name',
            'branch_code',
            'table_number',
            'capacity',
            'section',
            'is_active',
            'active_session_id',
            'qr_token',
            'created_at',
            'updated_at',
        ]

    def get_qr_token(self, obj):
        return generate_table_qr_token(obj)


class DiningTableCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiningTable
        fields = ['table_number', 'capacity', 'section', 'is_active']


class TableQRResolvedContextSerializer(serializers.Serializer):
    table_id = serializers.IntegerField(source='id')
    table_number = serializers.CharField()
    section = serializers.CharField()
    capacity = serializers.IntegerField()
    branch_id = serializers.IntegerField(source='branch.id')
    branch_code = serializers.CharField(source='branch.branch_code')
    branch_name = serializers.CharField(source='branch.name')
    restaurant_name = serializers.CharField(source='branch.restaurant.name')
    active_session_id = serializers.UUIDField(allow_null=True)

