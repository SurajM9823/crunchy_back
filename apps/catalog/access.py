from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from apps.restaurants.models import Branch
from apps.user_accounts.models import UserRole


def resolve_branch(request, *, manage=False, operational=False):
    user = request.user
    raw = request.query_params.get('outlet_id') or request.data.get('outlet_id')
    if not raw and user.is_authenticated:
        raw = user.branch_id
    if not raw or not str(raw).isdigit():
        raise ValidationError({'outlet_id': 'An explicit numeric outlet_id is required.'})
    branch = Branch.objects.select_related('restaurant').filter(pk=raw, is_active=True).first()
    if not branch:
        raise NotFound('Outlet not found.')
    if operational and not manage:
        if not user.is_authenticated or not user.is_active:
            raise PermissionDenied('Staff login is required for the POS menu.')
        if not user.is_superuser and not (
            user.branch_id == branch.pk and user.role != UserRole.CUSTOMER
            or user.restaurant_id == branch.restaurant_id and user.role == UserRole.RESTAURANT_OWNER
        ):
            raise PermissionDenied('POS access is limited to your assigned outlet.')
    if manage:
        if not user.is_authenticated or not user.is_active:
            raise PermissionDenied()
        if not user.is_superuser:
            owner = user.role == UserRole.RESTAURANT_OWNER and user.restaurant_id == branch.restaurant_id
            manager = user.role == UserRole.BRANCH_MANAGER and user.branch_id == branch.id
            employee = getattr(user, 'employee_profile', None)
            delegated = bool(employee and employee.is_active and user.branch_id == branch.id and
                             'menu' in (employee.assigned_pages or []))
            if not (owner or manager or delegated):
                raise PermissionDenied('Menu access is limited to your assigned outlet.')
    return branch
