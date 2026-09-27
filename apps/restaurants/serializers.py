from rest_framework import serializers
from apps.user_accounts.serializers import UserOutputSerializer
from .models import Restaurant, Branch


class BranchSerializer(serializers.ModelSerializer):
    """Serializer for franchise branch outlets."""
    manager_detail = UserOutputSerializer(source='manager', read_only=True)

    class Meta:
        model = Branch
        fields = [
            'id',
            'restaurant',
            'name',
            'branch_code',
            'operate_type',
            'manager',
            'manager_detail',
            'enable_dine_in',
            'enable_takeaway',
            'enable_delivery',
            'enable_drive_thru',
            'enable_qr_ordering',
            'enable_kiosk',
            'enable_pos',
            'phone_number',
            'email',
            'address_line',
            'city',
            'state',
            'postal_code',
            'is_main_branch',
            'is_active',
            'accepting_orders',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class RestaurantListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing restaurant brands."""
    admin_detail = UserOutputSerializer(source='admin', read_only=True)
    branches_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Restaurant
        fields = [
            'id',
            'name',
            'slug',
            'admin',
            'admin_detail',
            'pan_number',
            'phone',
            'email',
            'website',
            'logo_url',
            'description',
            'is_active',
            'branches_count',
            'created_at',
        ]
        read_only_fields = fields


class RestaurantDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for a restaurant brand including all franchise branches."""
    admin_detail = UserOutputSerializer(source='admin', read_only=True)
    branches = BranchSerializer(many=True, read_only=True)

    class Meta:
        model = Restaurant
        fields = [
            'id',
            'name',
            'slug',
            'admin',
            'admin_detail',
            'pan_number',
            'phone',
            'email',
            'website',
            'logo_url',
            'description',
            'is_active',
            'branches',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class RestaurantCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a restaurant brand."""
    class Meta:
        model = Restaurant
        fields = [
            'id',
            'name',
            'slug',
            'admin',
            'pan_number',
            'phone',
            'email',
            'website',
            'logo_url',
            'description',
            'is_active',
        ]
        read_only_fields = ['id', 'slug']


class BranchCreateSerializer(serializers.ModelSerializer):
    """Serializer for adding a branch to a restaurant."""
    class Meta:
        model = Branch
        fields = [
            'id',
            'name',
            'branch_code',
            'operate_type',
            'manager',
            'enable_dine_in',
            'enable_takeaway',
            'enable_delivery',
            'enable_drive_thru',
            'enable_qr_ordering',
            'enable_kiosk',
            'enable_pos',
            'phone_number',
            'email',
            'address_line',
            'city',
            'state',
            'postal_code',
            'is_main_branch',
            'is_active',
            'accepting_orders',
        ]
        read_only_fields = ['id']

