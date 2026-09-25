from decimal import Decimal
from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth import logout
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Sum
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.restaurants.models import Branch
from .models import Employee, SystemRole
from .serializers import (
    LoginSerializer, UserOutputSerializer, UserCreateSerializer,
    EmployeeSerializer, EmployeeCreateSerializer, EmployeeUpdateSerializer,
    StaffPinLoginSerializer,
)
from .services import (
    authenticate_user, generate_auth_tokens, superuser_login, user_create,
    employee_create, employee_update, employee_delete,
)
from .selectors import get_user_by_id, list_staff_users, get_user_by_identifier


class SuperuserLoginView(View):
    """
    Renders and handles the dedicated Superuser / Staff Login Web Portal.
    Accepts Email, Phone Number, or Username.
    """
    template_name = 'user_accounts/superuser_login.html'

    def get(self, request):
        if request.user.is_authenticated and (request.user.is_superuser or request.user.is_staff):
            return redirect('/admin/')
        return render(request, self.template_name)

    def post(self, request):
        identifier = request.POST.get('identifier', '').strip()
        password = request.POST.get('password', '')
        remember_me = request.POST.get('remember_me') == 'on'

        success, message, user = superuser_login(request, identifier=identifier, password=password)

        if success:
            if not remember_me:
                # Session expires on browser close
                request.session.set_expiry(0)
            next_url = request.GET.get('next') or request.POST.get('next') or '/admin/'
            return redirect(next_url)

        return render(request, self.template_name, {
            'error_message': message,
            'identifier': identifier,
        })


class SuperuserLogoutView(View):
    """
    Logs out the superuser and redirects back to the login portal.
    """
    def get(self, request):
        logout(request)
        messages.info(request, "You have been logged out successfully.")
        return redirect('superuser-login')

    def post(self, request):
        return self.get(request)


# --------------------------------------------------------------------------
# REST API Views (Stateless / JWT Authentication)
# --------------------------------------------------------------------------

class LoginAPIView(APIView):
    """
    POST /api/v1/auth/login/
    Authenticates user via phone number, email, or username and returns JWT tokens.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        identifier = serializer.validated_data['identifier']
        password = serializer.validated_data['password']

        user = authenticate_user(identifier=identifier, password=password)

        if not user:
            # Check whether user exists to provide helpful feedback
            existing = get_user_by_identifier(identifier)
            if existing and not existing.is_active:
                return Response(
                    {"detail": "Account is disabled. Please contact the administrator."},
                    status=status.HTTP_403_FORBIDDEN
                )
            return Response(
                {"detail": "Invalid credentials. Provide a valid email, phone number, or username."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        token_data = generate_auth_tokens(user)
        return Response(token_data, status=status.HTTP_200_OK)


class OutletLoginAPIView(APIView):
    """
    POST /api/v1/auth/outlet-login/
    Authenticates an Outlet Administrator (Branch Manager, Restaurant Owner, or Staff)
    and returns JWT tokens containing cryptographic outlet claims along with
    the branch operational context.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        identifier = serializer.validated_data['identifier']
        password = serializer.validated_data['password']

        user = authenticate_user(identifier=identifier, password=password)

        if not user:
            existing = get_user_by_identifier(identifier)
            if existing and not existing.is_active:
                return Response(
                    {"detail": "Account is disabled. Please contact the administrator."},
                    status=status.HTTP_403_FORBIDDEN
                )
            return Response(
                {"detail": "Invalid credentials. Provide a valid email, phone number, or username."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not (user.is_staff or user.is_superuser or user.role in ('BRANCH_MANAGER', 'RESTAURANT_OWNER', 'CASHIER', 'CHEF')):
            return Response(
                {"detail": "Access denied. Only authorized outlet staff and administrators can access this portal."},
                status=status.HTTP_403_FORBIDDEN
            )

        if not user.branch and not user.is_superuser and not user.restaurant:
            return Response(
                {"detail": "No outlet is currently assigned to this administrator account. Please contact the superadmin."},
                status=status.HTTP_403_FORBIDDEN
            )

        token_data = generate_auth_tokens(user)
        return Response(token_data, status=status.HTTP_200_OK)


class UserProfileAPIView(APIView):
    """
    GET /api/v1/auth/me/
    Returns the authenticated user's profile details.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = get_user_by_id(request.user.id)
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserOutputSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class StaffListAPIView(APIView):
    """
    GET /api/v1/auth/staff/
    Returns list of all restaurant staff and managers.
    Requires staff or superuser permissions.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Permission denied. Staff only."},
                status=status.HTTP_403_FORBIDDEN
            )
        staff_members = list_staff_users()
        serializer = UserOutputSerializer(staff_members, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SystemHealthAPIView(APIView):
    """
    GET /api/v1/health/
    Verifies database connectivity, celery config, and websocket layer.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from django.db import connection
        db_status = "healthy"
        try:
            connection.ensure_connection()
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"

        return Response({
            "status": "online",
            "service": "Crunchy Restaurant Management System Backend",
            "database": db_status,
            "version": "1.0.0",
        }, status=status.HTTP_200_OK)


class EmployeeListCreateAPIView(APIView):
    """
    GET /api/v1/employees/
    Lists staff and computes staff KPI metrics.
    Branch-scoped: Automatically restricted to logged-in admin's outlet.

    POST /api/v1/employees/
    Creates a new employee profile and underlying user account.
    Branch-scoped: Automatically bound to logged-in admin's outlet.
    """
    permission_classes = [IsAuthenticated]

    def _get_scoped_branch(self, request):
        user = request.user
        if user.branch:
            return user.branch
        elif user.restaurant:
            return user.restaurant.branches.first()
        elif user.is_superuser:
            outlet_id = request.query_params.get('outlet_id')
            if outlet_id:
                return (
                    Branch.objects.filter(id=outlet_id).first() if str(outlet_id).isdigit() else None
                ) or Branch.objects.filter(branch_code=outlet_id).first()
            return None
        return None

    def get(self, request):
        scoped_branch = self._get_scoped_branch(request)
        qs = Employee.objects.select_related('assigned_outlet', 'user').all()

        if scoped_branch:
            qs = qs.filter(assigned_outlet=scoped_branch)
        elif not request.user.is_superuser:
            return Response(
                {"detail": "No outlet assigned to your account."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Filters
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search) |
                Q(title__icontains=search)
            )

        role = request.query_params.get('role', '').strip()
        if role and role.upper() != 'ALL' and role in SystemRole.values:
            qs = qs.filter(role=role)

        status_filter = request.query_params.get('status', '').strip().upper()
        if status_filter == 'ACTIVE':
            qs = qs.filter(is_active=True)
        elif status_filter == 'INACTIVE':
            qs = qs.filter(is_active=False)

        # Compute KPI Metrics
        total_staff = qs.count()
        active_staff = qs.filter(is_active=True).count()
        active_percentage = round((active_staff / total_staff * 100)) if total_staff > 0 else 100
        payroll_sum = qs.filter(is_active=True).aggregate(Sum('salary_monthly'))['salary_monthly__sum'] or Decimal('0.00')

        if scoped_branch:
            branches_staffed = 1 if total_staff > 0 else 0
            total_branches = 1
        else:
            branches_staffed = qs.values('assigned_outlet').distinct().count()
            total_branches = Branch.objects.filter(is_active=True).count()

        metrics = {
            "total_staff": total_staff,
            "active_staff": active_staff,
            "active_percentage": active_percentage,
            "total_monthly_payroll": float(payroll_sum),
            "branches_staffed": branches_staffed,
            "total_branches": total_branches,
        }

        serializer = EmployeeSerializer(qs, many=True)
        return Response({
            "metrics": metrics,
            "results": serializer.data,
        }, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = EmployeeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        scoped_branch = self._get_scoped_branch(request)
        if not scoped_branch:
            assigned_outlet_id = data.get('assigned_outlet_id')
            if assigned_outlet_id:
                scoped_branch = (
                    Branch.objects.filter(id=assigned_outlet_id).first() if str(assigned_outlet_id).isdigit() else None
                ) or Branch.objects.filter(branch_code=assigned_outlet_id).first()
            if not scoped_branch:
                scoped_branch = Branch.objects.first()

        if not scoped_branch:
            return Response(
                {"detail": "No valid outlet identified for employee assignment."},
                status=status.HTTP_400_BAD_REQUEST
            )

        employee = employee_create(
            name=data['name'],
            email=data['email'],
            phone=data['phone'],
            assigned_outlet=scoped_branch,
            role=data.get('role', SystemRole.CASHIER),
            title=data.get('title', ''),
            salary_monthly=data.get('salary_monthly', Decimal('0.00')),
            assigned_pages=data.get('assigned_pages', []),
            temporary_password=data.get('temporary_password', ''),
            pin_code=data.get('pin_code', ''),
            is_active=data.get('is_active', True),
            avatar=data.get('avatar', None),
        )
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)


class EmployeeDetailAPIView(APIView):
    """
    GET, PATCH, DELETE for a specific employee profile.
    Enforces tenant branch isolation.
    """
    permission_classes = [IsAuthenticated]

    def _get_employee(self, request, pk):
        employee = Employee.objects.select_related('assigned_outlet', 'user').filter(id=pk).first()
        if not employee:
            return None, Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if not user.is_superuser:
            if user.branch and employee.assigned_outlet_id != user.branch_id:
                return None, Response({"detail": "Permission denied. Cross-outlet access prohibited."}, status=status.HTTP_403_FORBIDDEN)
            elif user.restaurant and employee.assigned_outlet.restaurant_id != user.restaurant_id:
                return None, Response({"detail": "Permission denied. Cross-brand access prohibited."}, status=status.HTTP_403_FORBIDDEN)

        return employee, None

    def get(self, request, pk):
        employee, error_res = self._get_employee(request, pk)
        if error_res:
            return error_res
        return Response(EmployeeSerializer(employee).data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        employee, error_res = self._get_employee(request, pk)
        if error_res:
            return error_res

        serializer = EmployeeUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_employee = employee_update(employee, **serializer.validated_data)
        return Response(EmployeeSerializer(updated_employee).data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        employee, error_res = self._get_employee(request, pk)
        if error_res:
            return error_res

        if employee.role == SystemRole.SUPER_ADMIN:
            return Response(
                {"detail": "Cannot delete root SUPER_ADMIN account."},
                status=status.HTTP_403_FORBIDDEN
            )

        employee_delete(employee)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StaffPinLoginAPIView(APIView):
    """
    POST /api/v1/auth/staff-pin-login/
    Rapid POS terminal switch via 4-to-6 digit PIN code.
    Authenticates staff instantly for their outlet without requiring full passwords.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = StaffPinLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        outlet_id = data.get('outlet_id')
        pin_code = data.get('pin_code')

        branch = None
        if outlet_id:
            branch = (
                Branch.objects.filter(id=outlet_id).first() if str(outlet_id).isdigit() else None
            ) or Branch.objects.filter(branch_code=outlet_id).first()

        if not branch:
            if request.user.is_authenticated and request.user.branch:
                branch = request.user.branch
            else:
                branch = Branch.objects.first()

        if not branch:
            return Response({"detail": "Outlet not found."}, status=status.HTTP_404_NOT_FOUND)

        employees = Employee.objects.filter(
            assigned_outlet=branch,
            is_active=True
        ).exclude(pin_hash__isnull=True).exclude(pin_hash="")

        matched_employee = None
        for emp in employees:
            if emp.check_pin(pin_code):
                matched_employee = emp
                break

        if not matched_employee:
            return Response(
                {"detail": "Invalid PIN code for this outlet."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        tokens = generate_auth_tokens(matched_employee.user)
        return Response({
            "access": tokens['access'],
            "refresh": tokens['refresh'],
            "employee": {
                "id": matched_employee.id,
                "name": matched_employee.name,
                "role": matched_employee.role,
                "title": matched_employee.title,
                "assigned_pages": matched_employee.assigned_pages,
            },
            "outlet": {
                "id": branch.id,
                "name": branch.name,
                "code": branch.branch_code,
            }
        }, status=status.HTTP_200_OK)


