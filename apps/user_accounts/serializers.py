from decimal import Decimal
from rest_framework import serializers
from apps.restaurants.models import Branch
from .models import User, UserRole, Employee, SystemRole, AdminSubPage, ROLE_PRESET_MODULES


class LoginSerializer(serializers.Serializer):
    """
    Validates login credentials using any identifier:
    Email, Phone Number, or Username.
    """
    identifier = serializers.CharField(
        required=True,
        help_text="Provide phone number (+1234567890), email address, or username."
    )
    password = serializers.CharField(
        required=True,
        write_only=True,
        style={'input_type': 'password'}
    )


class UserOutputSerializer(serializers.ModelSerializer):
    """
    Read-only serializer for user profile representation.
    """
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    employee_id = serializers.SerializerMethodField()
    assigned_pages = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'phone_number',
            'role',
            'role_display',
            'employee_id',
            'assigned_pages',
            'is_superuser',
            'is_staff',
            'is_verified',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_employee_id(self, obj):
        profile = getattr(obj, 'employee_profile', None)
        return profile.id if profile else None

    def get_assigned_pages(self, obj):
        profile = getattr(obj, 'employee_profile', None)
        return profile.assigned_pages if profile else []


class AssignedOutletSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source='branch_code', read_only=True)

    class Meta:
        model = Branch
        fields = ['id', 'name', 'code']


class EmployeeSerializer(serializers.ModelSerializer):
    assigned_outlet = AssignedOutletSerializer(read_only=True)
    joined_date = serializers.SerializerMethodField()

    class Meta:
        model = Employee
        fields = [
            'id',
            'name',
            'email',
            'phone',
            'role',
            'title',
            'assigned_outlet',
            'salary_monthly',
            'assigned_pages',
            'is_active',
            'joined_date',
            'avatar',
            'created_at',
        ]
        read_only_fields = ['id', 'joined_date', 'created_at']

    def get_joined_date(self, obj):
        if not obj.joined_date:
            return None
        if hasattr(obj.joined_date, 'strftime'):
            return obj.joined_date.strftime('%Y-%m-%d')
        return str(obj.joined_date)


class EmployeeCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=32)
    role = serializers.ChoiceField(choices=SystemRole.choices, default=SystemRole.CASHIER)
    title = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    assigned_outlet_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    salary_monthly = serializers.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), required=False)
    assigned_pages = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    is_active = serializers.BooleanField(default=True, required=False)
    temporary_password = serializers.CharField(required=False, allow_blank=True, default="")
    pin_code = serializers.CharField(required=False, allow_blank=True, default="")
    avatar = serializers.URLField(required=False, allow_null=True, default=None)


class EmployeeUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    email = serializers.EmailField(required=False)
    phone = serializers.CharField(max_length=32, required=False)
    role = serializers.ChoiceField(choices=SystemRole.choices, required=False)
    title = serializers.CharField(max_length=120, required=False, allow_blank=True)
    salary_monthly = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    assigned_pages = serializers.ListField(child=serializers.CharField(), required=False)
    is_active = serializers.BooleanField(required=False)
    temporary_password = serializers.CharField(required=False, allow_blank=True)
    pin_code = serializers.CharField(required=False, allow_blank=True)
    avatar = serializers.URLField(required=False, allow_null=True)


class StaffPinLoginSerializer(serializers.Serializer):
    outlet_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    pin_code = serializers.CharField(required=True, min_length=4, max_length=8)


class UserCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating restaurant users / staff.
    """
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'phone_number',
            'password',
            'role',
            'first_name',
            'last_name',
        ]

    def validate(self, attrs):
        username = attrs.get('username')
        email = attrs.get('email')
        phone = attrs.get('phone_number')
        if not username and not email and not phone:
            raise serializers.ValidationError(
                "Must provide at least one identifier: username, email, or phone number."
            )
        return attrs

