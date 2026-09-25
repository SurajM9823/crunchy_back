import uuid
from decimal import Decimal
from typing import Optional, Tuple, List
from django.db import transaction
from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from apps.common.utils import normalize_phone_number
from apps.restaurants.models import Branch
from .models import User, UserRole, Employee, SystemRole, ROLE_PRESET_MODULES
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
    Generates SimpleJWT access and refresh tokens with cryptographic claims
    and returns user and outlet operational payload.
    """
    refresh = RefreshToken.for_user(user)
    # Add custom claims to the JWT payload
    # Add custom claims to the JWT payload (Stateless verification at 10k scale)
    refresh['role'] = user.role
    refresh['is_superuser'] = user.is_superuser
    refresh['is_staff'] = user.is_staff
    refresh['restaurant_id'] = user.restaurant_id
    refresh['branch_id'] = user.branch_id

    emp_profile = getattr(user, 'employee_profile', None)
    if emp_profile:
        refresh['employee_id'] = emp_profile.id
        refresh['assigned_pages'] = emp_profile.assigned_pages

    outlet_data = None
    if user.branch:
        b = user.branch
        refresh['branch_code'] = b.branch_code
        refresh['operate_type'] = b.operate_type
        outlet_data = {
            'id': b.id,
            'name': b.name,
            'branch_code': b.branch_code,
            'operate_type': b.operate_type,
            'operate_type_display': b.get_operate_type_display(),
            'is_active': b.is_active,
            'accepting_orders': b.accepting_orders,
            'channels': {
                'dine_in': b.enable_dine_in,
                'takeaway': b.enable_takeaway,
                'delivery': b.enable_delivery,
                'drive_thru': b.enable_drive_thru,
                'qr_ordering': b.enable_qr_ordering,
                'kiosk': b.enable_kiosk,
                'pos': b.enable_pos,
            },
            'restaurant': {
                'id': b.restaurant_id,
                'name': b.restaurant.name if b.restaurant else '',
                'slug': b.restaurant.slug if b.restaurant else '',
            } if b.restaurant else None,
        }

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
            'employee_id': emp_profile.id if emp_profile else None,
            'assigned_pages': emp_profile.assigned_pages if emp_profile else [],
            'is_superuser': user.is_superuser,
            'is_staff': user.is_staff,
            'restaurant_id': user.restaurant_id,
            'branch_id': user.branch_id,
        },
        'outlet': outlet_data,
    }


SYSTEM_ROLE_TO_USER_ROLE = {
    SystemRole.SUPER_ADMIN: UserRole.SUPERADMIN,
    SystemRole.STORE_MANAGER: UserRole.BRANCH_MANAGER,
    SystemRole.CASHIER: UserRole.CASHIER,
    SystemRole.KITCHEN_SUPERVISOR: UserRole.CHEF,
    SystemRole.INVENTORY_MANAGER: UserRole.BRANCH_MANAGER,
    SystemRole.FLOOR_STAFF: UserRole.WAITER,
}


def generate_employee_id() -> str:
    count = Employee.objects.count() + 1
    new_id = f"emp-{count:02d}"
    if Employee.objects.filter(id=new_id).exists():
        new_id = f"emp-{count:02d}-{uuid.uuid4().hex[:4]}"
    return new_id


@transaction.atomic
def employee_create(
    *,
    name: str,
    email: str,
    phone: str,
    assigned_outlet: Branch,
    role: str = SystemRole.CASHIER,
    title: str = "",
    salary_monthly: Decimal = Decimal('0.00'),
    assigned_pages: Optional[List[str]] = None,
    temporary_password: Optional[str] = None,
    pin_code: Optional[str] = None,
    is_active: bool = True,
    avatar: Optional[str] = None,
) -> Employee:
    email = email.strip().lower()
    if User.objects.filter(email__iexact=email).exists() or Employee.objects.filter(email__iexact=email).exists():
        raise ValidationError(f"A staff member with email '{email}' already exists.")

    phone = phone.strip()
    if not assigned_pages:
        assigned_pages = list(ROLE_PRESET_MODULES.get(role, []))

    mapped_user_role = SYSTEM_ROLE_TO_USER_ROLE.get(role, UserRole.CASHIER)
    is_staff_flag = role in (SystemRole.SUPER_ADMIN, SystemRole.STORE_MANAGER)
    is_super_flag = (role == SystemRole.SUPER_ADMIN)

    base_username = email.split('@')[0]
    username = base_username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    pwd = temporary_password if (temporary_password and temporary_password.strip()) else f"Crunchy@{uuid.uuid4().hex[:6]}"

    name_parts = name.strip().split()
    first_name = name_parts[0] if name_parts else ""
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    user = User.objects.create_user(
        username=username,
        email=email,
        phone_number=phone,
        password=pwd,
        role=mapped_user_role,
        is_staff=is_staff_flag,
        is_superuser=is_super_flag,
        is_active=is_active,
        branch=assigned_outlet,
        restaurant=assigned_outlet.restaurant if assigned_outlet else None,
        first_name=first_name,
        last_name=last_name,
    )

    employee = Employee(
        id=generate_employee_id(),
        user=user,
        name=name.strip(),
        email=email,
        phone=phone,
        role=role,
        title=title.strip(),
        assigned_outlet=assigned_outlet,
        salary_monthly=Decimal(str(salary_monthly)),
        assigned_pages=assigned_pages,
        is_active=is_active,
        avatar=avatar,
    )
    if pin_code:
        employee.set_pin(pin_code)
    employee.save()
    return employee


@transaction.atomic
def employee_update(
    employee: Employee,
    **fields
) -> Employee:
    user = employee.user

    if 'name' in fields and fields['name']:
        employee.name = fields['name'].strip()
        parts = employee.name.split()
        user.first_name = parts[0] if parts else ""
        user.last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    if 'email' in fields and fields['email']:
        new_email = fields['email'].strip().lower()
        if new_email != employee.email:
            if User.objects.filter(email__iexact=new_email).exclude(id=user.id).exists():
                raise ValidationError(f"Email '{new_email}' is already taken.")
            employee.email = new_email
            user.email = new_email

    if 'phone' in fields and fields['phone']:
        employee.phone = fields['phone'].strip()
        user.phone_number = employee.phone

    if 'title' in fields:
        employee.title = fields['title'].strip()

    if 'role' in fields and fields['role']:
        employee.role = fields['role']
        user.role = SYSTEM_ROLE_TO_USER_ROLE.get(employee.role, UserRole.CASHIER)
        user.is_staff = employee.role in (SystemRole.SUPER_ADMIN, SystemRole.STORE_MANAGER)
        user.is_superuser = (employee.role == SystemRole.SUPER_ADMIN)

    if 'salary_monthly' in fields and fields['salary_monthly'] is not None:
        employee.salary_monthly = Decimal(str(fields['salary_monthly']))

    if 'assigned_pages' in fields and fields['assigned_pages'] is not None:
        employee.assigned_pages = fields['assigned_pages']

    if 'is_active' in fields and fields['is_active'] is not None:
        employee.is_active = fields['is_active']
        user.is_active = fields['is_active']

    if 'avatar' in fields:
        employee.avatar = fields['avatar']

    if 'pin_code' in fields and fields['pin_code']:
        employee.set_pin(fields['pin_code'])

    if 'temporary_password' in fields and fields['temporary_password']:
        user.set_password(fields['temporary_password'])

    user.save()
    employee.save()
    return employee


@transaction.atomic
def employee_delete(employee: Employee):
    if employee.role == SystemRole.SUPER_ADMIN:
        raise ValidationError("Cannot delete root SUPER_ADMIN account.")
    user = employee.user
    employee.delete()
    if user:
        user.delete()

