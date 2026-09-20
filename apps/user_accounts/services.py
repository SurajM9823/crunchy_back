from typing import Optional, Tuple
from django.db import transaction
from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from apps.common.utils import normalize_phone_number
from .models import User, UserRole
from .selectors import get_user_by_identifier


@transaction.atomic
def user_create(
    *,
    username: Optional[str] = None,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    password: Optional[str] = None,
    role: str = UserRole.CUSTOMER,
    is_staff: bool = False,
    is_superuser: bool = False,
    **extra_fields
) -> User:
    """
    Creates a new user record with validation.
    Enforces uniqueness and normalizes fields.
    """
    if not username and not email and not phone_number:
        raise ValidationError('At least one of username, email, or phone number must be provided.')

    if email:
        email = email.strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(f"A user with email '{email}' already exists.")

    if phone_number:
        phone_number = normalize_phone_number(phone_number)
        if User.objects.filter(phone_number=phone_number).exists():
            raise ValidationError(f"A user with phone number '{phone_number}' already exists.")

    if username:
        username = username.strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(f"A user with username '{username}' already exists.")

    user = User.objects.create_user(
        username=username,
        email=email,
        phone_number=phone_number,
        password=password,
        role=role,
        is_staff=is_staff,
        is_superuser=is_superuser,
        **extra_fields
    )
    return user


@transaction.atomic
def superuser_create(
    *,
    username: Optional[str] = None,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    password: Optional[str] = None,
    **extra_fields
) -> User:
    """
    Creates a new superuser with Super Administrator role and all staff/superuser flags.
    """
    return user_create(
        username=username,
        email=email,
        phone_number=phone_number,
        password=password,
        role=UserRole.SUPERADMIN,
        is_staff=True,
        is_superuser=True,
        is_verified=True,
        **extra_fields
    )


def authenticate_user(identifier: str, password: str) -> Optional[User]:
    """
    Authenticates a user against any identifier (phone, email, or username).
    """
    return authenticate(username=identifier, password=password)


def superuser_login(request, identifier: str, password: str) -> Tuple[bool, str, Optional[User]]:
    """
    Validates superuser credentials and establishes a Django session.
    Only allows users with `is_superuser=True` or `is_staff=True`.
    """
    if not identifier or not password:
        return False, "Please provide both identifier and password.", None

    user = authenticate(request, username=identifier, password=password)

    if not user:
        # Check if user exists to provide helpful feedback
        existing = get_user_by_identifier(identifier)
        if existing and not existing.is_active:
            return False, "This account is disabled. Please contact the administrator.", None
        return False, "Invalid credentials. Please verify your phone, email, or username and password.", None

    if not (user.is_superuser or user.is_staff):
        return False, "Access denied. Only superusers and authorized staff can access this portal.", None

    login(request, user, backend='apps.user_accounts.backends.MultiIdentifierAuthBackend')
    return True, "Login successful.", user


def generate_auth_tokens(user: User) -> dict:
    """
    Generates SimpleJWT access and refresh tokens along with user meta payload.
    """
    refresh = RefreshToken.for_user(user)
    # Add custom claims to the JWT payload
    refresh['role'] = user.role
    refresh['is_superuser'] = user.is_superuser
    refresh['is_staff'] = user.is_staff

    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'phone_number': user.phone_number,
            'role': user.role,
            'role_display': user.get_role_display(),
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
        }
    }

