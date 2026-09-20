from typing import Optional
from django.db.models import QuerySet
from .models import Restaurant, Branch


def get_restaurant_by_id(restaurant_id: int) -> Optional[Restaurant]:
    """Retrieve a restaurant brand by ID."""
    try:
        return Restaurant.objects.select_related('admin').get(pk=restaurant_id)
    except Restaurant.DoesNotExist:
        return None


def get_restaurant_by_slug(slug: str) -> Optional[Restaurant]:
    """Retrieve a restaurant brand by slug."""
    try:
        return Restaurant.objects.select_related('admin').get(slug=slug)
    except Restaurant.DoesNotExist:
        return None


def list_restaurants(active_only: bool = True) -> QuerySet[Restaurant]:
    """List restaurant brands, optionally filtering for active only."""
    qs = Restaurant.objects.select_related('admin').all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def list_branches_by_restaurant(restaurant_id: int, active_only: bool = True) -> QuerySet[Branch]:
    """List all franchise outlets for a given restaurant brand."""
    qs = Branch.objects.select_related('restaurant', 'manager').filter(restaurant_id=restaurant_id)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def get_branch_by_id(branch_id: int) -> Optional[Branch]:
    """Retrieve a branch outlet by primary key."""
    try:
        return Branch.objects.select_related('restaurant', 'manager').get(pk=branch_id)
    except Branch.DoesNotExist:
        return None


def get_branch_by_code(branch_code: str) -> Optional[Branch]:
    """Retrieve a branch outlet by its unique operational code (e.g. 'CB-KTM-001')."""
    if not branch_code:
        return None
    return Branch.objects.select_related('restaurant', 'manager').filter(branch_code__iexact=branch_code.strip()).first()


def list_all_branches(active_only: bool = True) -> QuerySet[Branch]:
    """List all branches across all brands."""
    qs = Branch.objects.select_related('restaurant', 'manager').all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs

