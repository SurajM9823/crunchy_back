"""Shared account resolution for customer signup, login, and recovery."""
from rest_framework.exceptions import ValidationError
from apps.user_accounts.models import User
from .models import CustomerProfile


def phone_users(phone, *, lock=False):
    queryset = User.objects.select_for_update() if lock else User.objects
    return list(queryset.filter(phone_number__in=[phone, phone[1:], phone[4:]]))


def can_complete_signup(user):
    return (
        user.role == 'CUSTOMER'
        and not user.is_staff and not user.is_superuser
        and not hasattr(user, 'employee_profile')
        and (not user.password or not user.has_usable_password())
        and not CustomerProfile.objects.filter(user=user).exists()
    )


def resolve_account(phone, *, lock=False):
    users = phone_users(phone, lock=lock)
    if len(users) > 1:
        raise ValidationError({'code': 'account_conflict', 'detail':
            'More than one account uses this mobile number. Please contact the outlet to resolve it.'})
    if not users:
        return None, 'signup'
    user = users[0]
    if (user.role != 'CUSTOMER' or user.is_staff or user.is_superuser
            or hasattr(user, 'employee_profile')):
        raise ValidationError({'code': 'staff_account', 'detail':
            'This number belongs to a staff account. Use staff sign-in or contact the outlet.'})
    if can_complete_signup(user):
        return user, 'signup'
    if not user.is_active:
        raise ValidationError({'code': 'account_disabled', 'detail':
            'This customer account is disabled. Please contact the outlet to reactivate it.'})
    return user, 'login'
