"""Staff permissions for POS, billing and kitchen operations."""
from rest_framework.exceptions import PermissionDenied, ValidationError
from apps.restaurants.models import Branch
from apps.user_accounts.models import UserRole


def can_access(user, branch, capability='orders'):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if capability == 'delete_order':
        if user.is_superuser or user.role == UserRole.SUPERADMIN:
            return True
        if user.role == UserRole.RESTAURANT_OWNER:
            return user.restaurant_id == branch.restaurant_id
        employee = getattr(user, 'employee_profile', None)
        return bool(employee and employee.is_active and employee.assigned_outlet_id == branch.pk
                    and employee.role == 'SUPER_ADMIN' and 'pos' in (employee.assigned_pages or []))
    if user.is_superuser or user.role == UserRole.SUPERADMIN:
        return True
    if user.role == UserRole.RESTAURANT_OWNER:
        return user.restaurant_id == branch.restaurant_id
    employee = getattr(user, 'employee_profile', None)
    if employee:
        if not employee.is_active or employee.assigned_outlet_id != branch.pk:
            return False
        pages = set(employee.assigned_pages or [])
        if capability in ('daybook', 'daybook_void'):
            return 'daybook' in pages and (capability == 'daybook' or employee.role in ('STORE_MANAGER','SUPER_ADMIN'))
        if capability in ('loyalty', 'loyalty_manage'):
            return 'loyalty' in pages and (capability == 'loyalty' or employee.role in ('STORE_MANAGER','SUPER_ADMIN'))
        if capability in ('discount', 'refund', 'loyalty_manage', 'daybook_void'):
            return employee.role in ('STORE_MANAGER','SUPER_ADMIN') and 'pos' in pages
        if capability == 'billing':
            return 'pos' in pages and employee.role in ('CASHIER','STORE_MANAGER','SUPER_ADMIN')
        return bool(pages & ({'pos','kitchen'} if capability in ('read','kitchen') else {'pos'}))
    if user.branch_id != branch.pk:
        return False
    if user.role == UserRole.BRANCH_MANAGER:
        return True
    if capability in ('discount', 'refund', 'loyalty_manage', 'daybook_void'):
        return False
    if user.role == UserRole.CASHIER:
        return True
    if user.role == UserRole.CHEF:
        return capability in ('read', 'kitchen')
    if user.role == UserRole.WAITER:
        return capability in ('read', 'orders', 'kitchen')
    return False


def require_access(user, branch, capability='orders'):
    if not can_access(user, branch, capability):
        raise PermissionDenied('Your account cannot perform this action at this outlet.')


def staff_branch(request, capability='read'):
    raw = request.query_params.get('outlet_id') or request.user.branch_id
    if not raw or not str(raw).isdigit():
        raise ValidationError({'outlet_id': 'Choose an outlet.'})
    branch = Branch.objects.select_related('restaurant').filter(pk=raw, is_active=True, restaurant__is_active=True).first()
    if not branch:
        raise PermissionDenied('Outlet is unavailable.')
    require_access(request.user, branch, capability)
    return branch
