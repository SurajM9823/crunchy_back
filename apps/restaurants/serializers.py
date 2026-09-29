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


class OrganizationSerializer(serializers.ModelSerializer):
    """
    Serializer for Organization Profile, IRD Nepal Fiscal Tax settings,
    Brand Logo upload, and Payment Gateways.
    Supports both multipart/form-data (logo file upload) and JSON payloads.
    """
    logo = serializers.ImageField(required=False, allow_null=True)
    logo_url = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = Restaurant
        fields = [
            'id',
            'name',
            'legal_name',
            'slug',
            'logo',
            'payment_qr',
            'logo_url',
            'phone',
            'email',
            'website',
            'address',
            'description',
            'currency',
            'currency_symbol',
            # Fiscal / IRD Tax Rules
            'pan_number',
            'vat_rate_percent',
            'is_vat_enabled',
            'service_charge_percent',
            'is_service_charge_enabled',
            'ird_bill_prefix',
            'fiscal_year',
            'ird_software_id',
            'ird_enable_realtime_sync',
            # Payment Gateways
            'enable_cash',
            'enable_card',
            'enable_fonepay',
            'enable_esewa',
            'enable_khalti',
            # Status & Timestamps
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']

    def validate_payment_qr(self, value):
        if value and (value.size > 5*1024*1024 or value.image.format not in ('JPEG', 'PNG', 'WEBP')):
            raise serializers.ValidationError('Use a PNG, JPEG, or WebP QR image under 5 MB.')
        return value

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        request = self.context.get('request')
        if instance.logo:
            try:
                if request:
                    ret['logo_url'] = request.build_absolute_uri(instance.logo.url)
                else:
                    ret['logo_url'] = instance.logo.url
            except Exception:
                ret['logo_url'] = instance.logo_url or ""
        elif instance.logo_url:
            if request and instance.logo_url.startswith('/'):
                ret['logo_url'] = request.build_absolute_uri(instance.logo_url)
            else:
                ret['logo_url'] = instance.logo_url
        else:
            ret['logo_url'] = ""
        return ret


