from rest_framework.permissions import BasePermission
from apps.user_accounts.models import UserRole, SystemRole, AdminSubPage


class IsOutletAdminOrStaff(BasePermission):
    """
    Enforces strict Outlet Isolation (Tenant Isolation):
    - Superusers have access to all outlets.
    - Restaurant Owners have access to all outlets under their brand.
    - Outlet Admins (Branch Managers) and Staff ONLY have access to their assigned outlet.
    - Rejects any cross-outlet read or write requests.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_active):
            return False

        if request.user.is_superuser:
            return True

        # Must have an assigned branch or restaurant
        return bool(request.user.branch_id or request.user.restaurant_id)

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False

        if request.user.is_superuser:
            return True

        # Identify branch object
        branch = getattr(obj, 'branch', obj)
        if hasattr(branch, 'restaurant_id'):
            # Check if user is the Brand Owner
            if request.user.restaurant_id and request.user.restaurant_id == branch.restaurant_id:
                if request.user.role == UserRole.RESTAURANT_OWNER or request.user.is_staff:
                    return True

            # Check if user is assigned to this specific branch
            if request.user.branch_id and request.user.branch_id == branch.id:
                return True

        return False


class IsOutletAdminOnly(BasePermission):
    """
    Strict permission allowing only the assigned Branch Manager (Outlet Admin),
    the Brand Owner, or Superadmin to modify operational settings.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_active):
            return False

        if request.user.is_superuser:
            return True

        return request.user.role in (UserRole.BRANCH_MANAGER, UserRole.RESTAURANT_OWNER) or request.user.is_staff


class IsOrganizationAdminOrManager(BasePermission):
    """
    Strict permission allowing Outlet Admins, Store Managers, Brand Owners,
    and Superusers to access and update Organization profile and fiscal rules.
    Also verifies RBAC 'organization' module permission for employees.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.is_active):
            return False

        if request.user.is_superuser:
            return True

        if request.user.role in (UserRole.RESTAURANT_OWNER, UserRole.BRANCH_MANAGER) or request.user.is_staff:
            return True

        # Check employee RBAC
        employee = getattr(request.user, 'employee_profile', None)
        if employee and employee.is_active:
            if employee.role in (SystemRole.SUPER_ADMIN, SystemRole.STORE_MANAGER):
                return True
            if AdminSubPage.ORGANIZATION.value in (employee.assigned_pages or []):
                return True

        return False


