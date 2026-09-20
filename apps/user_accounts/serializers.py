from rest_framework import serializers
from .models import User, UserRole


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

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'phone_number',
            'role',
            'role_display',
            'is_superuser',
            'is_staff',
            'is_verified',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


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

