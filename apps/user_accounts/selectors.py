from typing import Optional
from django.db.models import Q, QuerySet
from apps.common.utils import normalize_phone_number, detect_identifier_type
from .models import User, UserRole


def get_user_by_id(user_id: int) -> Optional[User]:
    """Retrieve a single user by primary key."""
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return None


def get_user_by_email(email: str) -> Optional[User]:
    """Retrieve a single user by email (case-insensitive)."""
    if not email:
        return None
    return User.objects.filter(email__iexact=email.strip()).first()


def get_user_by_phone(phone_number: str) -> Optional[User]:
    """Retrieve a single user by normalized phone number."""
    if not phone_number:
        return None
    cleaned = normalize_phone_number(phone_number)
    return User.objects.filter(phone_number=cleaned).first()


def get_user_by_username(username: str) -> Optional[User]:
    """Retrieve a single user by username (case-insensitive)."""
    if not username:
        return None
    return User.objects.filter(username__iexact=username.strip()).first()


def get_user_by_identifier(identifier: str) -> Optional[User]:
    """
    Find user by any valid identifier: email, phone number, or username.
    Smartly queries based on identifier format, with fallback to unified query.
    """
    if not identifier:
        return None

    identifier_str = str(identifier).strip()
    id_type = detect_identifier_type(identifier_str)

    if id_type == 'email':
        user = get_user_by_email(identifier_str)
        if user:
            return user

    if id_type == 'phone':
        user = get_user_by_phone(identifier_str)
        if user:
            return user

    # Default/fallback query across username, email, and normalized phone
    cleaned_phone = normalize_phone_number(identifier_str)
    query = Q(username__iexact=identifier_str) | Q(email__iexact=identifier_str)
    if cleaned_phone:
        query |= Q(phone_number=cleaned_phone)

    return User.objects.filter(query).first()


def list_active_users() -> QuerySet[User]:
    """List all active users."""
    return User.objects.filter(is_active=True)


def list_staff_users() -> QuerySet[User]:
    """List all staff and admin users."""
    return User.objects.filter(
        Q(is_staff=True) | Q(is_superuser=True) | ~Q(role=UserRole.CUSTOMER)
    ).filter(is_active=True)


def list_users_by_role(role: str) -> QuerySet[User]:
    """List all active users with a specific role."""
    return User.objects.filter(role=role, is_active=True)

