"""Shared account resolution for customer signup, login, and recovery."""
from rest_framework.exceptions import ValidationError
from apps.user_accounts.models import User
from .models import CustomerProfile


def phone_users(phone, *, lock=False):
    queryset = User.objects.select_for_update() if lock else User.objects
    return list(queryset.filter(phone_number__in=[phone, phone[1:], phone[4:]]))


def is_staff_account(user):
    return (user.role != 'CUSTOMER' or user.is_staff or user.is_superuser
            or hasattr(user, 'employee_profile'))


def require_customer_recovery(user):
    if user and is_staff_account(user):
        raise ValidationError({'code': 'staff_recovery_required', 'detail':
            'You can order with your staff password. To reset it, use staff account recovery or contact your administrator.'})


def can_complete_signup(user):
    return (
        not is_staff_account(user)
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
    if can_complete_signup(user):
        return user, 'signup'
    if not user.is_active:
        raise ValidationError({'code': 'account_disabled', 'detail':
            'This customer account is disabled. Please contact the outlet to reactivate it.'})
    return user, 'login'
