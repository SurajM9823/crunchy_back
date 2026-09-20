from typing import Optional, Tuple
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.user_accounts.models import User, UserRole
from apps.user_accounts.services import user_create
from .models import Restaurant, Branch


@transaction.atomic
def restaurant_create(
    *,
    name: str,
    admin_user: User,
    pan_number: str = '',
    phone: str = '',
    email: str = '',
    website: str = '',
    logo_url: str = '',
    description: str = '',
    is_active: bool = True,
) -> Restaurant:
    """
    Creates a new Restaurant Brand (Franchise parent).
    Ensures admin user has appropriate RESTAURANT_OWNER role and is linked.
    """
    name = name.strip()
    if not name:
        raise ValidationError('Restaurant brand name cannot be blank.')

    if Restaurant.objects.filter(name__iexact=name).exists():
        raise ValidationError(f"A restaurant brand named '{name}' already exists.")

    restaurant = Restaurant.objects.create(
        name=name,
        admin=admin_user,
        pan_number=pan_number.strip(),
        phone=phone.strip(),
        email=email.strip(),
        website=website.strip(),
        logo_url=logo_url.strip(),
        description=description.strip(),
        is_active=is_active,
    )

    # Elevate and link admin user if not superuser
    if not admin_user.is_superuser:
        admin_user.role = UserRole.RESTAURANT_OWNER
        admin_user.is_staff = True
    admin_user.restaurant = restaurant
    admin_user.save(update_fields=['role', 'is_staff', 'restaurant'])

    return restaurant


@transaction.atomic
def restaurant_admin_create(
    *,
    restaurant_name: str,
    admin_username: Optional[str] = None,
    admin_email: Optional[str] = None,
    admin_phone: Optional[str] = None,
    admin_password: str = None,
    pan_number: str = '',
    phone: str = '',
    email: str = '',
    **extra_brand_fields
) -> Tuple[Restaurant, User]:
    """
    Atomic creation of both the Restaurant Brand and its primary Administrator user.
    """
    admin_user = user_create(
        username=admin_username,
        email=admin_email,
        phone_number=admin_phone,
        password=admin_password,
        role=UserRole.RESTAURANT_OWNER,
        is_staff=True,
        is_verified=True,
    )

    restaurant = restaurant_create(
        name=restaurant_name,
        admin_user=admin_user,
        pan_number=pan_number,
        phone=phone or admin_phone or '',
        email=email or admin_email or '',
        **extra_brand_fields
    )

    return restaurant, admin_user


@transaction.atomic
def branch_create(
    *,
    restaurant: Restaurant,
    name: str,
    branch_code: str,
    manager: Optional[User] = None,
    phone_number: str = '',
    email: str = '',
    address_line: str = '',
    city: str = 'Kathmandu',
    state: str = 'Bagmati',
    postal_code: str = '',
    is_main_branch: bool = False,
    is_active: bool = True,
    accepting_orders: bool = True,
) -> Branch:
    """
    Creates a new franchise outlet / branch for a parent restaurant brand.
    """
    name = name.strip()
    branch_code = branch_code.strip().upper()

    if not name or not branch_code:
        raise ValidationError('Both branch name and branch code are required.')

    if Branch.objects.filter(branch_code=branch_code).exists():
        raise ValidationError(f"A branch with code '{branch_code}' already exists.")

    if Branch.objects.filter(restaurant=restaurant, name__iexact=name).exists():
        raise ValidationError(f"A branch named '{name}' already exists for {restaurant.name}.")

    if is_main_branch:
        # Unset other main branches for this restaurant
        Branch.objects.filter(restaurant=restaurant, is_main_branch=True).update(is_main_branch=False)

    branch = Branch.objects.create(
        restaurant=restaurant,
        name=name,
        branch_code=branch_code,
        manager=manager,
        phone_number=phone_number.strip(),
        email=email.strip(),
        address_line=address_line.strip(),
        city=city.strip(),
        state=state.strip(),
        postal_code=postal_code.strip(),
        is_main_branch=is_main_branch,
        is_active=is_active,
        accepting_orders=accepting_orders,
    )

    if manager:
        manager.branch = branch
        manager.restaurant = restaurant
        if manager.role == UserRole.CUSTOMER:
            manager.role = UserRole.BRANCH_MANAGER
            manager.is_staff = True
        manager.save(update_fields=['branch', 'restaurant', 'role', 'is_staff'])

    return branch


@transaction.atomic
def restaurant_update(restaurant: Restaurant, **fields) -> Restaurant:
    """Updates restaurant brand fields."""
    for field, value in fields.items():
        if hasattr(restaurant, field):
            setattr(restaurant, field, value)
    restaurant.save()
    return restaurant


@transaction.atomic
def branch_update(branch: Branch, **fields) -> Branch:
    """Updates branch outlet fields."""
    if fields.get('is_main_branch'):
        Branch.objects.filter(restaurant=branch.restaurant, is_main_branch=True).exclude(pk=branch.pk).update(is_main_branch=False)
    for field, value in fields.items():
        if hasattr(branch, field):
            setattr(branch, field, value)
    branch.save()
    return branch

